"""
    Flowblade Movie Editor is a nonlinear video editor.
    Copyright 2012 Janne Liljeblad.

    This file is part of Flowblade Movie Editor <http://code.google.com/p/flowblade>.

    Flowblade Movie Editor is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    Flowblade Movie Editor is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with Flowblade Movie Editor.  If not, see <http://www.gnu.org/licenses/>.
"""

"""
Clip player used to select frames for preview and range selection.
"""


import mlt
import os
import re
import sys
import subprocess
import time

import mltprofiles
import userfolders
import utils

TICKER_DELAY = 0.25
RENDER_TICKER_DELAY = 0.05

_current_profile = None

def set_current_profile(clip_path):
    profile = mltprofiles.get_default_profile()
    producer = mlt.Producer(profile, str(clip_path))
    global _current_profile
    profile_index = mltprofiles.get_closest_matching_profile_index(utils.get_file_producer_info(producer))
    _current_profile = mltprofiles.get_profile_for_index(profile_index)
    return profile_index

def get_frames_range_writer_for_current_profile(file_path, callback):
    return FramesRangeWriter(file_path, callback, _current_profile)

        
class GmicPlayer:
    
    def __init__(self, clip_path):
        self.producer = mlt.Producer(_current_profile, str(clip_path))
        self.producer.mark_in = -1
        self.producer.mark_out = -1
        
    def create_sdl_consumer(self):
        """
        Creates consumer with sdl output to a gtk+ widget.
        """
        # Create consumer and set params
        self.consumer = mlt.Consumer(_current_profile, "sdl")
        self.consumer.set("real_time", 1)
        self.consumer.set("rescale", "bicubic") # MLT options "nearest", "bilinear", "bicubic", "hyper"
        self.consumer.set("resize", 1)
        self.consumer.set("progressive", 1)
        self.consumer.set("scrub_audio", 0)

        # Hold ref to switch back from rendering
        self.sdl_consumer = self.consumer 

    def refresh(self): # Window events need this to get picture back
        self.consumer.stop()
        self.consumer.start()

    def connect_and_start(self):
        """
        Connects current procer and consumer and
        """
        self.consumer.purge()
        self.producer.set_speed(0)
        self.consumer.connect(self.producer)
        self.consumer.start()

    def current_frame(self):
        return self.producer.frame()

    def get_active_length(self):
        return self.producer.get_length()
                
    def seek_position_normalized(self, pos, length):
        frame_number = pos * length
        self.seek_frame(int(frame_number)) 
    
    def seek_frame(self, frame):
        # Force range
        length = self.get_active_length()
        if frame < 0:
            frame = 0
        elif frame >= length:
            frame = length - 1

        #self.producer.set_speed(0)
        self.producer.seek(frame) 
    
    def seek_delta(self, delta):
        # Get new frame
        frame = self.producer.frame() + delta
        # Seek frame
        self.seek_frame(frame)
        
    def get_rgb_frame(self):
        frame = self.producer.get_frame()
        # And make sure we deinterlace if input is interlaced
        frame.set("consumer_deinterlace", 1)

        # Now we are ready to get the image and save it.
        size = (self.profile.width(), self.profile.height())
        rgb = frame.get_image(mlt.mlt_image_rgb24a, *size) 
        return rgb

    def shutdown(self):
        self.producer.set_speed(0)
        self.consumer.stop()


class PreviewFrameWriter:

    def __init__(self, file_path):
        self.producer = mlt.Producer(_current_profile, str(file_path))
            
    def write_frame(self, clip_folder, frame):
        """
        Writes thumbnail image from file producer
        """
        # Get data
        
        frame_path = clip_folder + "frame" + str(frame) +  ".png"

        # Create consumer
        consumer = mlt.Consumer(_current_profile, "avformat", frame_path)
        consumer.set("real_time", 0)
        consumer.set("vcodec", "png")

        frame_producer = self.producer.cut(frame, frame)

        # Connect and write image
        consumer.connect(frame_producer)
        consumer.run()
        
        
class FramesRangeWriter:

    def __init__(self, file_path, callback, profile):
        self.producer = mlt.Producer(profile, str(file_path))
        self.profile = profile
        self.callback = callback
        self.running = True

    def write_frames(self, clip_folder, frame_name, mark_in, mark_out):
        """
        Writes thumbnail image from file producer
        """
        # Get data
        render_path = clip_folder + frame_name + "_%04d." + "png"
        print("render_path", render_path, mark_in, mark_out)
        self.consumer = mlt.Consumer(self.profile, "avformat", str(render_path))
        self.consumer.set("real_time", -1)
        self.consumer.set("rescale", "bicubic")
        self.consumer.set("vcodec", "png")
    
        self.frame_producer = self.producer.cut(mark_in, mark_out)

        self.consumer.connect(self.frame_producer)
        self.frame_producer.set_speed(0)
        self.frame_producer.seek(0)
        self.frame_producer.set_speed(1)
        self.consumer.start()

        print("Rendering frames range")
                
        while self.running: # set false at shutdown() for abort
            if self.frame_producer.frame() >= mark_out:
                
                self.callback(self.frame_producer.frame() - mark_in)
                time.sleep(2.0) # This seems enough, other methods produced bad waits

                self.running = False
            else:
                self.callback(self.frame_producer.frame())
                time.sleep(0.2)
    
    def shutdown(self):
        if self.running == False:
            return

        self.consumer.stop()
        self.frame_producer.set_speed(0)
        self.running = False
        

class FolderFramesScriptRenderer:

    def __init__(self, user_script, folder, out_folder, frame_name, update_callback, render_output_callback):
        self.user_script = user_script
        self.folder = folder
        self.out_folder = out_folder
        self.frame_name = frame_name
        self.update_callback = update_callback
        self.render_output_callback = render_output_callback
        self.abort = False

    def write_frames(self):
        clip_frames = os.listdir(self.folder)

        frame_count = 1
        for clip_frame in clip_frames:

            if self.abort == True:
                return
            
            self.do_update_callback(frame_count)

            file_numbers_list = re.findall(r'\d+', clip_frame)
            filled_number_str = str(file_numbers_list[0]).zfill(3)

            clip_frame_path = os.path.join(self.folder, clip_frame)
            rendered_file_path = self.out_folder + self.frame_name + "_" + filled_number_str + ".png"
            
            script_str = "gmic " + clip_frame_path + " " + self.user_script + " -output " +  rendered_file_path

            if frame_count == 1: # first frame displays shell output and does error checking
                FLOG = open(userfolders.get_cache_dir() + "log_gmic_preview", 'w')
                p = subprocess.Popen(script_str, shell=True, stdin=FLOG, stdout=FLOG, stderr=FLOG)
                p.wait()
                FLOG.close()
 
                # read log
                f = open(userfolders.get_cache_dir() + "log_gmic_preview", 'r')
                out = f.read()
                f.close()

                self.do_render_output_callback(p, out)
            else:
                FLOG = open(userfolders.get_cache_dir() + "log_gmic_preview", 'w')
                p = subprocess.Popen(script_str, shell=True, stdin=FLOG, stdout=FLOG, stderr=FLOG)
                p.wait()
                FLOG.close()

            frame_count = frame_count + 1
    
    def do_update_callback(self, frame_count):
        self.update_callback(frame_count)

    def do_render_output_callback(self, process, out_text):
        self.render_output_callback(process, out_text)
                
    def abort(self):
        self.abort = True


# ---- Debug helper
def prints_to_log_file(log_file):
    so = se = open(log_file, 'w', buffering=1)

    sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1)

    os.dup2(so.fileno(), sys.stdout.fileno())
    os.dup2(se.fileno(), sys.stderr.fileno())
