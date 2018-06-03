#!/usr/bin/env python
# Created by br _at_ re-web _dot_ eu, 2015-2016

import os
from datetime import datetime
from glob import glob
from sys import exit
from time import sleep, time

from PIL import Image

from gui import GUI_PyGame as GuiModule
# from camera import CameraException, Camera_cv as CameraModule
from camera import CameraException, Camera_gPhoto as CameraModule
from slideshow import Slideshow
from events import Rpi_GPIO as GPIO

#####################
### Configuration ###
#####################

# Screen size
display_size = (848, 480)

# Maximum size of assembled image
# camera takes photos @ 4752x3168
image_size = (2352, 1568)

# Size of pictures in the assembled image
thumb_size = (1176, 784)

# (Processsed) image basename
picture_basename = datetime.now().strftime("%Y-%m-%d/processed")

# Original image basename
original_basename = datetime.now().strftime("%Y-%m-%d/original")

# GPIO channel of switch to shutdown the Photobooth
gpio_shutdown_channel = 24 # pin 18 in all Raspi-Versions

# GPIO channel of switch to take pictures
gpio_trigger_channel = 18 # pin 16 in all Raspi-Versions

# GPIO output channel for (blinking) lamp
gpio_lamp_channel = 23 # pin 7 in all Raspi-Versions

# Waiting time in seconds for posing
pose_time = 3

# Display time for assembled picture
display_time = 10

# Show a slideshow of existing pictures when idle
idle_slideshow = True

# Display time of pictures in the slideshow
slideshow_display_time = 5

# Temp directory for storing pictures
if os.access("/dev/shm", os.W_OK):
    tmp_dir = "/dev/shm/"       # Don't abuse Raspberry Pi SD card, if possible
    print("using shm")
else:
    tmp_dir = "/tmp/"
    print("using tmp")

###############
### Classes ###
###############

class PictureList:
    """A simple helper class.

    It provides the filenames for the assembled pictures and keeps count
    of taken and previously existing pictures.
    """

    def __init__(self, basename):
        """Initialize filenames to the given basename and search for
        existing files. Set the counter accordingly.
        """

        # Set basename and suffix
        self.basename = basename
        self.suffix = ".jpg"
        self.count_width = 5

        # Ensure directory exists
        dirname = os.path.dirname(self.basename)
        if not os.path.exists(dirname):
            os.makedirs(dirname)

        # Find existing files
        count_pattern = "[0-9]" * self.count_width
        pictures = glob(self.basename + count_pattern + self.suffix)

        # Get number of latest file
        if len(pictures) == 0:
            self.counter = 0
        else:
            pictures.sort()
            last_picture = pictures[-1]
            self.counter = int(last_picture[-(self.count_width+len(self.suffix)):-len(self.suffix)])

        # Print initial infos
        print("Info: Number of last existing file: " + str(self.counter))
        print("Info: Saving assembled pictures as: " + self.basename + "XXXXX.jpg")

    def get(self, count):
        return self.basename + str(count).zfill(self.count_width) + self.suffix

    def get_last(self):
        return self.get(self.counter)

    def get_next(self):
        self.counter += 1
        return self.get(self.counter)


class Photobooth:
    """The main class.

    It contains all the logic for the photobooth.
    """

    def __init__(self, display_size, picture_basename, picture_size, pose_time, display_time,
                 trigger_channel, shutdown_channel, lamp_channel, idle_slideshow, slideshow_display_time):
        self.display      = GuiModule('Photobooth', display_size)
        self.pictures     = PictureList(picture_basename)
        self.camera       = CameraModule(picture_size)

        self.pic_size     = picture_size
        self.pose_time    = pose_time
        self.display_time = display_time

        self.trigger_channel  = trigger_channel
        self.shutdown_channel = shutdown_channel
        self.lamp_channel     = lamp_channel

        self.idle_slideshow = idle_slideshow
        if self.idle_slideshow:
            self.slideshow_display_time = slideshow_display_time
            self.slideshow = Slideshow(display_size, display_time,
                                       os.path.dirname(os.path.realpath(picture_basename)))

        input_channels    = [ trigger_channel, shutdown_channel ]
        output_channels   = [ lamp_channel ]
        self.gpio         = GPIO(self.handle_gpio, input_channels, output_channels)

    def teardown(self):
        self.display.clear()
        self.display.show_message("Shutting down...")
        self.display.apply()
        self.gpio.set_output(self.lamp_channel, 0)
        sleep(0.5)
        self.display.teardown()
        self.gpio.teardown()
        self.remove_tempfiles()
        exit(0)

    def remove_tempfiles(self):
        for filename in glob(tmp_dir + "photobooth_*.jpg"):
            try:
                os.remove(filename)
            except OSError:
                pass


    def _run_plain(self):
        while True:
            self.camera.set_idle()

            # Display default message
            self.display.clear()
            self.display.show_message("Press the button!")
            self.display.apply()

            # Wait for an event and handle it
            event = self.display.wait_for_event()
            self.handle_event(event)

    def _run_slideshow(self):
        while True:
            self.camera.set_idle()
            self.slideshow.display_next("Press the button!")
            tic = time()
            while time() - tic < self.slideshow_display_time:
                self.check_and_handle_events()

    def run(self):
        while True:
            try:
                # Enable lamp
                self.gpio.set_output(self.lamp_channel, 1)

                # Select idle screen type
                if self.idle_slideshow:
                    self._run_slideshow()
                else:
                    self._run_plain()

            # Catch exceptions and display message
            except CameraException as e:
                self.handle_exception(e.message)
            # Do not catch KeyboardInterrupt and SystemExit
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                print('SERIOUS ERROR: ' + repr(e))
                self.handle_exception("SERIOUS ERROR!")

    def check_and_handle_events(self):
        r, e = self.display.check_for_event()
        while r:
            self.handle_event(e)
            r, e = self.display.check_for_event()

    def clear_event_queue(self):
        r, e = self.display.check_for_event()
        while r:
            r, e = self.display.check_for_event()

    def handle_gpio(self, channel):
        if channel in [ self.trigger_channel, self.shutdown_channel ]:
            self.display.trigger_event(channel)

    def handle_event(self, event):
        if event.type == 0:
            self.teardown()
        elif event.type == 1:
            self.handle_keypress(event.value)
        elif event.type == 2:
            self.handle_mousebutton(event.value[0], event.value[1])
        elif event.type == 3:
            self.handle_gpio_event(event.value)

    def handle_keypress(self, key):
        """Implements the actions for the different keypress events"""
        # Exit the application
        if key == ord('q'):
            self.teardown()
        # Take pictures
        elif key == ord('c'):
            self.take_picture()

    def handle_mousebutton(self, key, pos):
        """Implements the actions for the different mousebutton events"""
        # Take a picture
        if key == 1:
            print("Mouse click disabled")
            #self.take_picture()

    def handle_gpio_event(self, channel):
        """Implements the actions taken for a GPIO event"""
        if channel == self.trigger_channel:
            self.take_picture()
        elif channel == self.shutdown_channel:
            self.teardown()

    def handle_exception(self, msg):
        """Displays an error message and returns"""
        self.display.clear()
        print("Error: " + msg)
        self.display.show_message("ERROR:\n\n" + msg)
        self.display.apply()
        sleep(3)


    def assemble_pictures(self, input_filenames):
        """Assembles four pictures into a 2x2 grid

        It assumes, all original pictures have the same aspect ratio as
        the resulting image.

        For the thumbnail sizes we have:
        h = (H - 2 * a - 2 * b) / 2
        w = (W - 2 * a - 2 * b) / 2

                                    W
               |---------------------------------------|

          ---  +---+-------------+---+-------------+---+  ---
           |   |                                       |   |  a
           |   |   +-------------+   +-------------+   |  ---
           |   |   |             |   |             |   |   |
           |   |   |      0      |   |      1      |   |   |  h
           |   |   |             |   |             |   |   |
           |   |   +-------------+   +-------------+   |  ---
         H |   |                                       |   |  2*b
           |   |   +-------------+   +-------------+   |  ---
           |   |   |             |   |             |   |   |
           |   |   |      2      |   |      3      |   |   |  h
           |   |   |             |   |             |   |   |
           |   |   +-------------+   +-------------+   |  ---
           |   |                                       |   |  a
          ---  +---+-------------+---+-------------+---+  ---

               |---|-------------|---|-------------|---|
                 a        w       2*b       w        a
        """

        # Thumbnail size of pictures
        outer_border = 50
        inner_border = 20
        thumb_box = ( int( self.pic_size[0] / 2 ) ,
                      int( self.pic_size[1] / 2 ) )
        thumb_size = ( thumb_box[0] - outer_border - inner_border ,
                       thumb_box[1] - outer_border - inner_border )

        # Create output image with white background
        output_image = Image.new('RGB', self.pic_size, (255, 255, 255))

        # Image 0
        img = Image.open(input_filenames[0])
        img.thumbnail(thumb_size)
        offset = ( thumb_box[0] - inner_border - img.size[0] ,
                   thumb_box[1] - inner_border - img.size[1] )
        output_image.paste(img, offset)

        # Image 1
        img = Image.open(input_filenames[1])
        img.thumbnail(thumb_size)
        offset = ( thumb_box[0] + inner_border,
                   thumb_box[1] - inner_border - img.size[1] )
        output_image.paste(img, offset)

        # Image 2
        img = Image.open(input_filenames[2])
        img.thumbnail(thumb_size)
        offset = ( thumb_box[0] - inner_border - img.size[0] ,
                   thumb_box[1] + inner_border )
        output_image.paste(img, offset)

        # Image 3
        img = Image.open(input_filenames[3])
        img.thumbnail(thumb_size)
        offset = ( thumb_box[0] + inner_border ,
                   thumb_box[1] + inner_border )
        output_image.paste(img, offset)

        # Save assembled image
        # output_filename = self.pictures.get_next()
        output_filename = picture_basename + datetime.now().strftime("%H%M%S") + ".jpg"

        output_image.save(output_filename, "JPEG")
        return output_filename

    def show_counter(self, seconds):
        if self.camera.has_preview():

            # since changing clock() to time() the first countdown is a bit choppy,
            # presumably because it's the first time the camera is used, the latency
            # of which throws the timing off.
            self.display.clear()
            self.camera.take_preview(tmp_dir + "photobooth_preview.jpg")
            self.display.show_picture(tmp_dir + "photobooth_preview.jpg", flip=True)
            self.display.apply()

            tic = time()
            toc = time() - tic
            while toc < seconds:
                self.display.clear()
                self.camera.take_preview(tmp_dir + "photobooth_preview.jpg")
                self.display.show_picture(tmp_dir + "photobooth_preview.jpg", flip=True)
                self.display.show_message(str(seconds - int(toc)))
                self.display.apply()

                # Limit progress to 1 "second" per preview (e.g., too slow on Raspi 1)
                toc = min(toc + 1, time() - tic)
        else:
            for i in range(seconds):
                self.display.clear()
                self.display.show_message(str(seconds - i))
                self.display.apply()
                sleep(1)

    def take_picture(self):
        """Implements the picture taking routine"""
        # Disable lamp
        self.gpio.set_output(self.lamp_channel, 0)

        # Show pose message
        self.display.clear()
        self.display.show_message("POSE!\n\nFour pictures...");
        self.display.apply()
        sleep(2)

        # Extract display and image sizes
        size = self.display.get_size()
        outsize = (int(size[0]/2), int(size[1]/2))

        # Take pictures
        filenames = [i for i in range(4)]
        for x in range(4):
            # Countdown
            self.show_counter(self.pose_time)

            # Try each picture up to 3 times
            remaining_attempts = 3
            while remaining_attempts > 0:
                remaining_attempts = remaining_attempts - 1

                self.display.clear((255,230,200))
                self.display.show_message("SMILE!\nPhoto " + str(x+1) + " of 4")
                self.display.apply()

                tic = time()

                try:
                    # filenames[x] = self.camera.take_picture(tmp_dir + "photobooth_%02d.jpg" % x)
                    filenames[x] = self.camera.take_picture(original_basename + datetime.now().strftime("%H%M%S") + ".jpg")
                    remaining_attempts = 0
                except CameraException as e:
                    # On recoverable errors: display message and retry
                    if e.recoverable:
                        if remaining_attempts > 0:
                            self.display.clear()
                            self.display.show_message(e.message)
                            self.display.apply()
                            sleep(5)
                        else:
                            raise CameraException("Giving up! Please start over!", False)
                    else:
                       raise e

                # Measure used time and sleep a second if too fast
                toc = time() - tic
                if toc < 1.0:
                    sleep(1.0 - toc)

        # Show 'Wait'
        self.display.clear()
        self.display.show_message("Please wait!")
        self.display.apply()

        # Assemble them
        outfile = self.assemble_pictures(filenames)

        # Show pictures for 10 seconds
        self.display.clear()
        self.display.show_picture(outfile, size, (0,0))
        self.display.apply()
        sleep(self.display_time)

        # Reenable lamp
        self.gpio.set_output(self.lamp_channel, 1)

        # clear the event queue to handle button being pressed more than once
        self.clear_event_queue()

#################
### Functions ###
#################

def main():
    photobooth = Photobooth(display_size, picture_basename, image_size, pose_time, display_time,
                            gpio_trigger_channel, gpio_shutdown_channel, gpio_lamp_channel,
                            idle_slideshow, slideshow_display_time)
    photobooth.clear_event_queue()
    photobooth.run()
    photobooth.teardown()
    return 0

if __name__ == "__main__":
    exit(main())
