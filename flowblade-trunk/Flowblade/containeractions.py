"""
    Flowblade Movie Editor is a nonlinear video editor.
    Copyright 2012 Janne Liljeblad.

    This file is part of Flowblade Movie Editor <https://github.com/jliljebl/flowblade/>.

    Flowblade Movie Editor is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    Flowblade Movie Editor is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with Flowblade Movie Editor. If not, see <http://www.gnu.org/licenses/>.
"""
from gi.repository import Gtk
from gi.repository import Gdk
from gi.repository import GLib

import cairo
import copy
import hashlib
import json
try:
    import mlt7 as mlt
except:
    import mlt
import os
import subprocess
import shutil
import sys
import threading
import time

import appconsts
import atomicfile
import dialogutils
import edit
import editorstate
from editorstate import current_sequence
from editorstate import PROJECT
import fluxity
import fluxityheadless
import gui
import guiutils
import gmicheadless
import gmicplayer
import jobs
import mltprofiles
import mltxmlheadless
import renderconsumer
import rendergui
import respaths
import simpleeditors
import toolsencoding
import updater
import userfolders
import utils

"""
This module creates <ConatainerClipType>Actions wrapper objects for container clips data that are used to execute
all actions on container clips data.

Objects of class containerclips.ContainerData are persistent data for container clips, 
objects in this module created and discarded as needed.
"""

FULL_RENDER = 0
CLIP_LENGTH_RENDER = 1
PREVIEW_RENDER = 2

OVERLAY_COLOR = (0.17, 0.23, 0.63, 0.5)

GMIC_TYPE_ICON = None
MLT_XML_TYPE_ICON = None
BLENDER_TYPE_ICON = None # Deprecated
FLUXITY_TYPE_ICON = None

NEWLINE = '\n'

set_plugin_to_be_edited_func = None
get_edited_plugin_clip = None

# ----------------------------------------------------- interface
def get_action_object(container_data):
    if container_data.container_type == appconsts.CONTAINER_CLIP_GMIC:
        return GMicContainerActions(container_data)
    elif container_data.container_type == appconsts.CONTAINER_CLIP_MLT_XML:
         return MLTXMLContainerActions(container_data)
    elif container_data.container_type == appconsts.CONTAINER_CLIP_FLUXITY:
         return FluxityContainerActions(container_data)
         
# ------------------------------------------------------------ thumbnail creation helpers
def _get_type_icon(container_type):
    # TODO: When we get third move this into action objects.
    global GMIC_TYPE_ICON, MLT_XML_TYPE_ICON, FLUXITY_TYPE_ICON
    
    if GMIC_TYPE_ICON == None:
        GMIC_TYPE_ICON = cairo.ImageSurface.create_from_png(respaths.IMAGE_PATH + "container_clip_gmic.png")
    if MLT_XML_TYPE_ICON == None:
        MLT_XML_TYPE_ICON = cairo.ImageSurface.create_from_png(respaths.IMAGE_PATH + "container_clip_mlt_xml.png")
    if FLUXITY_TYPE_ICON == None:
        FLUXITY_TYPE_ICON = cairo.ImageSurface.create_from_png(respaths.IMAGE_PATH + "container_clip_fluxity.png")
        
    if container_type == appconsts.CONTAINER_CLIP_GMIC:
        return GMIC_TYPE_ICON
    elif container_type == appconsts.CONTAINER_CLIP_MLT_XML: 
        return MLT_XML_TYPE_ICON
    elif container_type == appconsts.CONTAINER_CLIP_FLUXITY:  
        return FLUXITY_TYPE_ICON
        
def _write_thumbnail_image(profile, file_path, action_object):
    """
    Writes thumbnail image from file producer
    """
    # Get data
    thumbnail_path = action_object.get_container_thumbnail_path()

    # Create consumer
    consumer = mlt.Consumer(profile, "avformat", 
                                 thumbnail_path)
    consumer.set("real_time", 0)
    consumer.set("vcodec", "png")

    # Create one frame producer
    producer = mlt.Producer(profile, str(file_path))

    info = utils.get_file_producer_info(producer)

    length = producer.get_length()
    frame = length // 2
    producer = producer.cut(frame, frame)

    # Connect and write image
    consumer.connect(producer)
    consumer.run()
    
    return (thumbnail_path, length, info)

def _create_image_surface(icon_path):
    icon = cairo.ImageSurface.create_from_png(icon_path)
    scaled_icon = cairo.ImageSurface(cairo.FORMAT_ARGB32, appconsts.THUMB_WIDTH, appconsts.THUMB_HEIGHT)
    cr = cairo.Context(scaled_icon)
    cr.save()
    cr.scale(float(appconsts.THUMB_WIDTH) / float(icon.get_width()), float(appconsts.THUMB_HEIGHT) / float(icon.get_height()))
    cr.set_source_surface(icon, 0, 0)
    cr.paint()
    cr.restore()

    return (cr, scaled_icon)


# ---------------------------------------------------- action objects
class AbstractContainerActionObject:
    
    def __init__(self, container_data):
        self.container_data = container_data
        self.render_type = -1 # to be set in methods below
        self.do_filters_clone = False
        
    def create_data_dirs_if_needed(self):
        session_folder = self.get_session_dir()
        clip_frames_folder = session_folder + appconsts.CC_CLIP_FRAMES_DIR
        rendered_frames_folder = session_folder + appconsts.CC_RENDERED_FRAMES_DIR 
        if not os.path.exists(session_folder):
            os.mkdir(session_folder)
        if not os.path.exists(clip_frames_folder):
            os.mkdir(clip_frames_folder)
        if not os.path.exists(rendered_frames_folder):
            os.mkdir(rendered_frames_folder)

    def validate_program(self):
        print("AbstractContainerActionObject.validate_program() not impl")

    def render_full_media(self, clip):
        self.render_type = FULL_RENDER
        self.clip = clip
        self.launch_render_data = (clip, 0, self.container_data.unrendered_length, 0)
        job_proxy = self.get_launch_job_proxy()
        jobs.add_job(job_proxy)
        
        # Render starts on callback from jobs.py
        
    def render_clip_length_media(self, clip):
        self.render_type = CLIP_LENGTH_RENDER
        self.clip = clip
        self.launch_render_data = (clip, clip.clip_in, clip.clip_out, clip.clip_in)

        job_proxy = self.get_launch_job_proxy()
        jobs.add_job(job_proxy)
        
        # Render starts on callback from jobs.py

    def render_preview(self, clip, frame, frame_start_offset):
        self.render_type = PREVIEW_RENDER
        self.clip = clip # This can be None because we are not doing a render update edit after render.
        self.launch_render_data = (clip, frame, frame, frame_start_offset)

        job_proxy = self.get_launch_job_proxy()
        jobs.add_job(job_proxy)

    def start_render(self):
        clip, range_in, range_out, clip_start_offset = self.launch_render_data
        self._launch_render(clip, range_in, range_out, clip_start_offset)

    def _launch_render(self, clip, range_in, range_out, clip_start_offset):
        print("AbstractContainerActionObject._launch_render() not impl")

    def switch_to_unrendered_media(self, rendered_clip):
        unrendered_clip = current_sequence().create_file_producer_clip(self.container_data.unrendered_media, new_clip_name=None, novalidate=True, ttl=1)
        track, clip_index = current_sequence().get_track_and_index_for_id(rendered_clip.id)

        data = {"old_clip":rendered_clip,
                "new_clip":unrendered_clip,
                "track":track,
                "index":clip_index,
                "do_filters_clone":self.do_filters_clone}
        action = edit.container_clip_switch_to_unrendered_replace(data)   
        action.do_edit()

    def get_session_dir(self):
        return self.get_container_clips_dir() + self.get_container_program_id()

    def get_rendered_media_dir(self):
        if self.container_data.render_data.save_internally == True:
            return self.get_session_dir() + appconsts.CC_RENDERED_FRAMES_DIR
        else:
            return self.container_data.render_data.render_dir + appconsts.CC_RENDERED_FRAMES_DIR

    def get_preview_media_dir(self):
        if self.container_data.render_data.save_internally == True:
            return self.get_session_dir() + appconsts.CC_PREVIEW_RENDER_DIR
        else:
            return self.container_data.render_data.render_dir + appconsts.CC_PREVIEW_RENDER_DIR

    def get_container_program_id(self):
        id_md_str = str(self.container_data.container_clip_uid) + str(self.container_data.container_type) + self.container_data.program + self.container_data.unrendered_media
        return hashlib.md5(id_md_str.encode('utf-8')).hexdigest() 

    def get_container_thumbnail_path(self):
        return userfolders.get_thumbnail_dir() + self.get_container_program_id() +  ".png"
    
    def get_job_proxy(self):
        print("AbstractContainerActionObject.get_job_proxy() not impl")

    def get_launch_job_proxy(self):
        job_proxy = self.get_job_proxy()
        job_proxy.status = jobs.QUEUED
        job_proxy.progress = 0.0
        job_proxy.elapsed = 0.0 # jobs does not use this value
        job_proxy.text = _("In Queue - ") + " " + self.get_job_name()
        return job_proxy

    def get_job_queue_message(self):
        job_proxy = self.get_job_proxy()
        job_queue_message = jobs.JobQueueMessage(   job_proxy.proxy_uid, job_proxy.type, job_proxy.status,
                                                    job_proxy.progress, job_proxy.text, job_proxy.elapsed)
        return job_queue_message

    def get_completed_job_message(self):
        job_msg = self.get_job_queue_message()
        job_msg.status = jobs.COMPLETED
        job_msg.progress = 1.0
        job_msg.elapsed = 0.0 # jobs does not use this value
        job_msg.text = "dummy" # this will be overwritten with completion message
        return job_msg

    def get_job_name(self):
        return "get_job_name not impl"
 
    def get_container_clips_dir(self):
        return userfolders.get_container_clips_dir()

    def get_lowest_numbered_file(self):
        frames_info = gmicplayer.FolderFramesInfo(self.get_rendered_media_dir())
        lowest = frames_info.get_lowest_numbered_file()
        highest = frames_info.get_highest_numbered_file()
        return frames_info.get_lowest_numbered_file()

    def get_rendered_frame_sequence_resource_path(self):
        frame_file = self.get_lowest_numbered_file() # Works for both external and internal
        if frame_file == None:
            # Something is quite wrong.
            print("No frame file found for container clip at:", self.get_rendered_media_dir())
            return None

        resource_name_str = utils.get_img_seq_resource_name(frame_file)
        return self.get_rendered_media_dir() + "/" + resource_name_str

    def get_rendered_video_clip_path(self):
        if self.container_data.render_data.save_internally == True:
            resource_path = self.get_session_dir() +  "/" + appconsts.CONTAINER_CLIP_VIDEO_CLIP_NAME + self.container_data.render_data.file_extension
        else:
            resource_path = self.container_data.render_data.render_dir +  "/" + self.container_data.render_data.file_name + self.container_data.render_data.file_extension
    
        return resource_path
    
    def get_rendered_thumbnail(self):
        thumbnail_path = self.get_container_thumbnail_path()
        if os.path.isfile(thumbnail_path) == True:
            surface = self._build_icon(thumbnail_path)
        else:
            surface, length, icon_path = self.create_icon()
        return surface

    def update_render_status(self):
        print("AbstractContainerActionObject.update_render_status not impl")

    def abort_render(self):
        print("AbstractContainerActionObject.abort_render not impl")

    def create_producer_and_do_update_edit(self, unused_data):

        # Using frame sequence as clip
        if  self.container_data.render_data.do_video_render == False:
            resource_path = self.get_rendered_frame_sequence_resource_path()
            if resource_path == None:
                return # TODO: User info?
                
            rendered_clip = current_sequence().create_file_producer_clip(resource_path, new_clip_name=self.clip.name, novalidate=False, ttl=1)

        # Using video clip as clip
        else:
            resource_path = self.get_rendered_video_clip_path()
            rendered_clip = current_sequence().create_file_producer_clip(resource_path, new_clip_name=self.clip.name, novalidate=True, ttl=1)
        
        track, clip_index = current_sequence().get_track_and_index_for_id(self.clip.id)
        
        # Check if container clip still on timeline
        if track == None:
            # clip was removed from timeline
            # TODO: infowindow?
            return
        
        # Do replace edit
        data = {"old_clip":self.clip,
                "new_clip":rendered_clip,
                "rendered_media_path":resource_path,
                "track":track,
                "index":clip_index,
                "do_filters_clone":self.do_filters_clone}
                
        if self.render_type == FULL_RENDER: # unrendered -> fullrender
            self.clip.container_data.last_render_type = FULL_RENDER
            action = edit.container_clip_full_render_replace(data)
            action.do_edit()
        else:  # unrendered -> clip render
            self.clip.container_data.last_render_type = CLIP_LENGTH_RENDER
            action = edit.container_clip_clip_render_replace(data)
            action.do_edit()

        return rendered_clip

    def set_video_endoding(self, clip, callback=None):
        self.external_encoding_callback = callback
        current_profile_index = mltprofiles.get_profile_index_for_profile(current_sequence().profile)
        # These need to re-initialized always when using this module.
        toolsencoding.create_widgets(current_profile_index, True, True)
        toolsencoding.widgets.file_panel.enable_file_selections(False)

        # Create default render data if not available, we need to know profile to do this.
        if self.container_data.render_data == None:
            self.container_data.render_data = toolsencoding.create_container_clip_default_render_data_object(current_sequence().profile)
            
        encoding_panel = toolsencoding.get_encoding_panel(self.container_data.render_data, True)

        align = dialogutils.get_default_alignment(encoding_panel)
        
        dialog = Gtk.Dialog(_("Container Clip Render Settings"),
                            gui.editor_window.window,
                            Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
                            (_("Cancel"), Gtk.ResponseType.REJECT,
                             _("Set Encoding"), Gtk.ResponseType.ACCEPT))
        dialog.vbox.pack_start(align, True, True, 0)
        dialogutils.set_outer_margins(dialog.vbox)
        dialog.set_resizable(False)

        dialog.connect('response', self.encode_settings_callback)
        dialog.show_all()

    def encode_settings_callback(self, dialog, response_id):
        if response_id == Gtk.ResponseType.ACCEPT:
            self.container_data.render_data = toolsencoding.get_render_data_for_current_selections()
            if self.external_encoding_callback != None:
                self.external_encoding_callback(self.container_data.render_data)

        dialog.destroy()
        
    def clone_clip(self, old_clip):
        new_container_data = copy.deepcopy(old_clip.container_data)
        new_container_data.generate_clip_id()
        
        new_clip_action_object = get_action_object(new_container_data)
        new_clip_action_object.create_data_dirs_if_needed()
        new_clip = new_clip_action_object.create_container_clip_media_clone(old_clip)
        new_clip.container_data = new_container_data
        return new_clip

    def create_container_clip_media_clone(self, container_clip):
        
        container_clip_action_object = get_action_object(container_clip.container_data)
        if container_clip.container_data.rendered_media == None:
            clone_clip = current_sequence().create_file_producer_clip(container_clip.path, None, False, container_clip.ttl)
        elif container_clip.container_data.render_data.do_video_render == True:
            # we have rendered a video clip for media last. 
            old_clip_path = container_clip_action_object.get_session_dir() + "/" + appconsts.CONTAINER_CLIP_VIDEO_CLIP_NAME + container_clip.container_data.render_data.file_extension
            new_clip_path = self.get_session_dir() + "/" + appconsts.CONTAINER_CLIP_VIDEO_CLIP_NAME + container_clip.container_data.render_data.file_extension
            shutil.copyfile(old_clip_path, new_clip_path)
            clone_clip =  current_sequence().create_file_producer_clip(new_clip_path, None, False, container_clip.ttl)
            
        else:
            # we have rendered a frame sequence clip for media last.
            old_frames_dir = container_clip_action_object.get_session_dir() + appconsts.CC_RENDERED_FRAMES_DIR
            new_frames_dir = self.get_session_dir() + appconsts.CC_RENDERED_FRAMES_DIR
            os.rmdir(new_frames_dir)
            shutil.copytree(old_frames_dir, new_frames_dir)
        
            resource_path = self.get_rendered_frame_sequence_resource_path()
            clone_clip =  current_sequence().create_file_producer_clip(resource_path, None, False, container_clip.ttl)

        return clone_clip

    def _create_icon_default_action(self):
        icon_path, length, info = _write_thumbnail_image(PROJECT().profile, self.container_data.unrendered_media, self)
        surface = self._build_icon(icon_path)
        return (surface, length, icon_path)
        
    def _build_icon(self, icon_path):
        cr, surface = _create_image_surface(icon_path)
        return surface
        
    def load_icon(self):
        return self._build_icon(self.get_container_thumbnail_path())
    
    def edit_program(sef, clip):
        print("AbstractContainerActionObject.edit_program not impl")

    def create_image_surface(self, icon_path):
        return _create_image_surface(icon_path)

class GMicContainerActions(AbstractContainerActionObject):

    def __init__(self, container_data):
        AbstractContainerActionObject.__init__(self, container_data)
        self.do_filters_clone = True
        self.parent_folder = userfolders.get_container_clips_dir()

    def validate_program(self):
        try:
            script_file = open(self.container_data.program)
            user_script = script_file.read()
            test_out_file = userfolders.get_cache_dir()  + "/gmic_cont_clip_test.png"
            test_in_file = str(respaths.IMAGE_PATH + "unrendered_blender.png") # we just need some valid input

            # Create command list and launch process.
            command_list = [editorstate.gmic_path, test_in_file]
            user_script_commands = user_script.split(" ")
            command_list.extend(user_script_commands)
            command_list.append("-output")
            command_list.append(test_out_file)

            # Render preview and write log
            FLOG = open(userfolders.get_cache_dir() + "gmic_container_validating_log", 'w')
            p = subprocess.Popen(command_list, stdin=FLOG, stdout=FLOG, stderr=FLOG)
            p.wait()
            FLOG.close()
         
            if p.returncode == 0:
                return (True, None) # no errors
            else:
                # read error log, and return.
                f = open(userfolders.get_cache_dir() + "gmic_container_validating_log", 'r')
                out = f.read()
                f.close()
                return (False, out)
    
        except Exception as e:
            return (False, str(e))
        
    def get_job_proxy(self):
        job_proxy = jobs.JobProxy(self.get_container_program_id(), self)
        job_proxy.type = jobs.CONTAINER_CLIP_RENDER_GMIC
        return job_proxy

    def get_job_name(self):
        return self.container_data.get_unrendered_media_name()

    def _launch_render(self, clip, range_in, range_out, gmic_frame_offset):
        self.create_data_dirs_if_needed()
        self.render_range_in = range_in
        self.render_range_out = range_out
        self.gmic_frame_offset = gmic_frame_offset
 
        gmicheadless.clear_flag_files(self.parent_folder, self.get_container_program_id())
    
        # We need data to be available for render process, 
        # create video_render_data object with default values if not available.
        if self.container_data.render_data == None:
            self.container_data.render_data = toolsencoding.create_container_clip_default_render_data_object(current_sequence().profile)
            
        gmicheadless.set_render_data(self.parent_folder, self.get_container_program_id(), self.container_data.render_data)
        
        job_msg = self.get_job_queue_message()
        job_msg.text = _("Render Starting...")
        job_msg.status = jobs.RENDERING
        jobs.update_job_queue(job_msg)
        
        args = ("session_id:" + self.get_container_program_id(), 
                "parent_folder:" + str(self.parent_folder), 
                "script:" + str(self.container_data.program),
                "clip_path:" + str(self.container_data.unrendered_media),
                "range_in:" + str(range_in),
                "range_out:"+ str(range_out),
                "profile_desc:" + PROJECT().profile.description().replace(" ", "_"),  # Here we have our own string space handling, maybe change later..
                "gmic_frame_offset:" + str(gmic_frame_offset))

        # Create command list and launch process.
        command_list = [sys.executable]
        command_list.append(respaths.LAUNCH_DIR + "flowbladegmicheadless")
        for arg in args:
            command_list.append(arg)

        subprocess.Popen(command_list)
        
    def update_render_status(self):
        GLib.idle_add(self._do_update_render_status)
            
    def _do_update_render_status(self):
                    
        if gmicheadless.session_render_complete(self.parent_folder, self.get_container_program_id()) == True:
            
            job_msg = self.get_completed_job_message()
            jobs.update_job_queue(job_msg)
            
            GLib.idle_add(self.create_producer_and_do_update_edit, None)

        else:
            status = gmicheadless.get_session_status(self.parent_folder, self.get_container_program_id())
            if status != None:
                step, frame, length, elapsed = status

                steps_count = 3
                if  self.container_data.render_data.do_video_render == False:
                    steps_count = 2
                msg = _("Step ") + str(step) + " / " + str(steps_count) + " - "
                if step == "1":
                    msg += _("Writing Clip Frames")
                elif step == "2":
                     msg += _("Rendering G'Mic Script")
                else:
                     msg += _("Encoding Video")
                
                msg += " - " + self.get_job_name()
                
                job_msg = self.get_job_queue_message()
                if self.render_type == FULL_RENDER:
                    job_msg.progress = float(frame)/float(length)
                else:
                    if step == "1":
                        render_length = self.render_range_out - self.render_range_in 
                        frame = int(frame) - self.gmic_frame_offset
                    else:
                        render_length = self.render_range_out - self.render_range_in
                    job_msg.progress = float(frame)/float(render_length)
                    
                    if job_msg.progress < 0.0:
                        # hack to fix how gmiplayer.FramesRangeWriter works.
                        # We would need to patch to G'mic Tool to not need this but this is easier.
                        job_msg.progress = 1.0

                    if job_msg.progress > 1.0:
                        # Fix how progress is calculated in gmicheadless because producers can render a bit longer then required.
                        job_msg.progress = 1.0

                job_msg.elapsed = float(elapsed)
                job_msg.text = msg
                
                jobs.update_job_queue(job_msg)
            else:
                pass # This can happen sometimes before gmicheadless.py has written a status message, we just do nothing here.

    def abort_render(self):
        gmicheadless.abort_render(self.parent_folder, self.get_container_program_id())

    def create_icon(self):
        icon_path, length, info = _write_thumbnail_image(PROJECT().profile, self.container_data.unrendered_media, self)
        cr, surface = _create_image_surface(icon_path)
        cr.rectangle(0, 0, appconsts.THUMB_WIDTH, appconsts.THUMB_HEIGHT)
        cr.set_source_rgba(*OVERLAY_COLOR)
        cr.fill()
        type_icon = _get_type_icon(appconsts.CONTAINER_CLIP_GMIC)
        cr.set_source_surface(type_icon, 1, 30)
        cr.set_operator (cairo.OPERATOR_OVERLAY)
        cr.paint_with_alpha(0.5)
        return (surface, length, icon_path)


class FluxityContainerActions(AbstractContainerActionObject):

    def __init__(self, container_data):
        AbstractContainerActionObject.__init__(self, container_data)
        self.do_filters_clone = True
        self.plugin_create_render_complete_callback = None # set at object creation site if needed
        self.parent_folder = userfolders.get_container_clips_dir()
        
    def validate_program(self):
        try:
            script_file = open(self.container_data.program)
            user_script = script_file.read()
            profile_file_path = mltprofiles.get_profile_file_path(current_sequence().profile.description())
            if self.container_data.unrendered_length == None:
                self.container_data.unrendered_length = 200

            fctx = fluxity.render_preview_frame(user_script, script_file, 0, self.container_data.unrendered_length, None, profile_file_path)
         
            if fctx.error == None:
                data_json = fctx.get_script_data()
                self.container_data.data_slots["fluxity_plugin_edit_data"] = json.loads(data_json) # script data saved as Python object, not json str.
                self.container_data.data_slots["fluxity_plugin_edit_data"] ["groups_list"] = fctx.groups

                return (True, None) # no errors
            else:
                return (False,  fctx.error)
    
        except Exception as e:
            return (False, str(e))
    
    def re_render_screenshot(self):
        script_file = open(self.container_data.program)
        user_script = script_file.read()
        profile_file_path = mltprofiles.get_profile_file_path(current_sequence().profile.description())
        frame = self.container_data.unrendered_length // 2
        screenshot_file = self.get_container_thumbnail_path()
        
        fctx = fluxity.render_preview_frame(user_script, script_file, frame, self.container_data.unrendered_length, None, profile_file_path)
        fctx.priv_context.frame_surface.write_to_png(screenshot_file)
        cr, surface = _create_image_surface(screenshot_file)
        return (screenshot_file, surface)

    def get_job_proxy(self):
        job_proxy = jobs.JobProxy(self.get_container_program_id(), self)
        job_proxy.type = jobs.CONTAINER_CLIP_RENDER_FLUXITY
        return job_proxy

    def get_job_name(self):
        data_object = self.container_data.data_slots["fluxity_plugin_edit_data"] 
        return data_object["name"]

    def _launch_render(self, clip, range_in, range_out, unused_frame_ofset):
        self.create_data_dirs_if_needed()
        
        range_out = range_out + 1 # MLT handles out frames inclusive but fluxity exclusive.
                                  # We just need to manually make sure we get always correct lengths.
        
        self.render_range_in = range_in
        self.render_range_out = range_out
        generator_length = self.container_data.unrendered_length

        fluxityheadless.clear_flag_files(self.parent_folder, self.get_container_program_id())
    
        # We need data to be available for render process, 
        # create video_render_data object with default values if not available.
        if self.container_data.render_data == None:
            self.container_data.render_data = toolsencoding.create_container_clip_default_render_data_object(current_sequence().profile)
            self.container_data.render_data.do_video_render = False 

        fluxityheadless.set_render_data(self.parent_folder, self.get_container_program_id(), self.container_data.render_data)
        
        job_msg = self.get_job_queue_message()
        job_msg.text = _("Render Starting...")
        job_msg.status = jobs.RENDERING
        jobs.update_job_queue(job_msg)

        # we could drop sending args if we wanted and just use this. 
        fluxityheadless.write_misc_session_data(self.parent_folder, self.get_container_program_id(), "fluxity_plugin_edit_data", self.container_data.data_slots["fluxity_plugin_edit_data"])

        args = ("session_id:" + self.get_container_program_id(),
                "parent_folder:" + self.parent_folder,
                "script:" + str(self.container_data.program),
                "generator_length:" + str(generator_length),
                "range_in:" + str(range_in),
                "range_out:"+ str(range_out),
                "profile_desc:" + PROJECT().profile.description().replace(" ", "_"))  # Here we have our own string space handling, maybe change later..

        t = CommandLauncherThread(respaths.LAUNCH_DIR + "flowbladefluxityheadless", args)
        t.start()
        
    def update_render_status(self):
        GLib.idle_add(self._do_update_render_status)
            
    def _do_update_render_status(self):
        
        # We need to set these None so that when render is complete tlinewidgets.py drawing code no longer thinks
        # that render is in progress
        self.container_data.progress = None
        self.clip.container_data.progress = None
        
        if fluxityheadless.session_render_complete(self.parent_folder, self.get_container_program_id()) == True:
            job_msg = self.get_completed_job_message()
            jobs.update_job_queue(job_msg)
            
            if self.plugin_create_render_complete_callback == None:
                # Completed render for timeline container clip update is handled here.
                GLib.idle_add(self.plugin_tline_render_comlete)
                GLib.idle_add(self.create_producer_and_do_update_edit, None)
            else:
                # Completed render for adding Generator plugin as rendered video clip is handled here. 
                if self.container_data.render_data.do_video_render == False:
                    resource_path = self.get_rendered_frame_sequence_resource_path()
                else:
                    resource_path = self.get_rendered_video_clip_path()

                GLib.idle_add(self.plugin_create_render_complete_callback, resource_path, self.container_data)

        else:
            status = fluxityheadless.get_session_status(self.parent_folder, self.get_container_program_id())
            if status != None:
                step, frame, length, elapsed = status

                steps_count = 2
                if self.container_data.render_data.do_video_render == False:
                    steps_count = 1
                msg = _("Step ") + str(step) + " / " + str(steps_count) + " - "
                if step == "1":
                    msg += _("Writing Clip Frames")
                else:
                     msg += _("Encoding Video")
                
                msg += " - " + self.get_job_name()
                
                job_msg = self.get_job_queue_message()
                if self.render_type == FULL_RENDER:
                    job_msg.progress = float(frame)/float(length)
                else:
                    if step == "1":
                        render_length = self.render_range_out - self.render_range_in
                    job_msg.progress = float(frame)/float(render_length)
                    
                    if job_msg.progress < 0.0:
                        # hack to fix how gmiplayer.FramesRangeWriter works.
                        # We would need to patch to G'mic Tool to not need this but this is easier.
                        job_msg.progress = 1.0

                    if job_msg.progress > 1.0:
                        # Fix how progress is calculated in rendering process because producers can render a bit longer then required.
                        job_msg.progress = 1.0

                job_msg.elapsed = float(elapsed)
                job_msg.text = msg
                
                jobs.update_job_queue(job_msg)

                # Update tline render % display, we need to set different attrs for 
                # first and succeeding renders.
                self.container_data.progress = job_msg.progress
                self.clip.container_data.progress = job_msg.progress
                updater.repaint_tline()
                
            else:
                pass # This can happen sometimes before gmicheadless.py has written a status message, we just do nothing here.


    def plugin_tline_render_comlete(self):
        clip = self.create_producer_and_do_update_edit(None)
        # Reopen in edit panel, doing callback to avoid circular imports
        set_plugin_to_be_edited_func(clip, self)

    def abort_render(self):
        fluxityheadless.abort_render(self.get_container_program_id())

    def create_icon(self):
        if self.container_data.data_slots["icon_file"] == None:
            icon_path, not_used_length, info = _write_thumbnail_image(PROJECT().profile, self.container_data.unrendered_media, self)
        else:
            icon_path, not_used_length, info = _write_thumbnail_image(PROJECT().profile, self.container_data.data_slots["icon_file"], self)

        cr, surface = _create_image_surface(icon_path)
 
        data_object = self.container_data.data_slots["fluxity_plugin_edit_data"]
        length = data_object["length"]

        return (surface, length, icon_path)

    def edit_program(self, clip):
        set_plugin_to_be_edited_func(clip, self)
        gui.editor_window.edit_multi.set_visible_child_name(appconsts.EDIT_MULTI_PLUGINS)

    def apply_editors(self, editors):
        new_editors_list = self.get_editors_data_as_editors_list(editors)
        self.container_data.data_slots["fluxity_plugin_edit_data"]["editors_list"] = new_editors_list
        get_edited_plugin_clip().container_data.data_slots["fluxity_plugin_edit_data"]["editors_list"] = new_editors_list

    def render_fluxity_preview(self, callbacks, editors, preview_frame):
        self.create_data_dirs_if_needed() # This could be first time we are writing
                                          # data related to this container clip to disk.
        
        completed_callback, error_callback = callbacks
        new_editors_list = self.get_editors_data_as_editors_list(editors)
        editors_data_json = json.dumps(new_editors_list)
        script_file = open(self.container_data.program)
        user_script = script_file.read()
        profile_file_path = mltprofiles.get_profile_file_path(current_sequence().profile.description())

        self.container_data.render_data = toolsencoding.create_container_clip_default_render_data_object(current_sequence().profile)
        self.container_data.render_data.do_video_render = False 
        
        out_folder = self.get_preview_media_dir()
        if not os.path.exists(out_folder):
            os.mkdir(out_folder)

        fctx = fluxity.render_preview_frame(user_script, script_file, preview_frame, self.container_data.unrendered_length, out_folder, profile_file_path, editors_data_json)
        if fctx.error != None:
            error_callback(fctx.error)
            return
                    
        fctx.priv_context.write_out_frame(True)
        
        completed_callback()

    def get_editors_data_as_editors_list(self, editor_widgets):
        new_editors_list = [] # This is the editors list in format created in
                              # fluxity.FluxityContext.get_script_data()
        for editor in editor_widgets:
            value = editor.get_value()
            # Special casing required for editors that have different internal representation of data
            # compared to what they give to out scripts.
            # BE VERY CAREFUL IF ADDING ANY NEW SUCH EDITORS.
            if editor.editor_type == simpleeditors.SIMPLE_EDITOR_COLOR:
                value = editor.get_value_as_color_tuple()
            elif editor.editor_type == simpleeditors.SIMPLE_EDITOR_FLOAT_RANGE or editor.editor_type == simpleeditors.SIMPLE_EDITOR_INT_RANGE:
                value = editor.get_value_as_range_tuple()
            elif editor.editor_type == simpleeditors.SIMPLE_EDITOR_OPTIONS:
                value = editor.get_value_as_tuple()

            new_editor = [editor.id_data, editor.editor_type, value]
            new_editors_list.append(new_editor)
        
        return new_editors_list


class MLTXMLContainerActions(AbstractContainerActionObject):

    def __init__(self, container_data):
        AbstractContainerActionObject.__init__(self, container_data)
        self.do_filters_clone = True
        self.parent_folder = userfolders.get_container_clips_dir()
        
    def validate_program(self):
        # These are created by application and are quaranteed to be valid.
        # This method is not even called.
        return True
        
    def get_job_proxy(self):
        job_proxy = jobs.JobProxy(self.get_container_program_id(), self)
        job_proxy.type = jobs.CONTAINER_CLIP_RENDER_MLT_XML
        return job_proxy

    def get_job_name(self):
        return self.container_data.get_unrendered_media_name()

    def _launch_render(self, clip, range_in, range_out, clip_start_offset):
        self.create_data_dirs_if_needed()
        self.render_range_in = range_in
        self.render_range_out = range_out
        self.clip_start_offset = clip_start_offset
 
        mltxmlheadless.clear_flag_files(self.parent_folder, self.get_container_program_id())
    
        # We need data to be available for render process, 
        # create video_render_data object with default values if not available.
        if self.container_data.render_data == None:
            self.container_data.render_data = toolsencoding.create_container_clip_default_render_data_object(current_sequence().profile)
            
        mltxmlheadless.set_render_data(self.parent_folder, self.get_container_program_id(), self.container_data.render_data)
        
        job_msg = self.get_job_queue_message()
        job_msg.text = _("Render Starting...")
        job_msg.status = jobs.RENDERING
        jobs.update_job_queue(job_msg)

        args = ("session_id:" + self.get_container_program_id(),
                "parent_folder:" + str(self.parent_folder),
                "clip_path:" + str(self.container_data.unrendered_media),
                "range_in:" + str(range_in),
                "range_out:"+ str(range_out),
                "profile_desc:" + PROJECT().profile.description().replace(" ", "_"),
                "xml_file_path:" + str(self.container_data.unrendered_media))

        # Create command list and launch process.
        command_list = [sys.executable]
        command_list.append(respaths.LAUNCH_DIR + "flowblademltxmlheadless")
        for arg in args:
            command_list.append(arg)

        subprocess.Popen(command_list)

    def update_render_status(self):
        GLib.idle_add(self._do_update_render_status)
            
    def _do_update_render_status(self):
                    
        if mltxmlheadless.session_render_complete(self.parent_folder, self.get_container_program_id()) == True:
            #self.remove_as_status_polling_object()

            job_msg = self.get_completed_job_message()
            jobs.update_job_queue(job_msg)
            
            GLib.idle_add(self.create_producer_and_do_update_edit, None)
                
        else:
            status = mltxmlheadless.get_session_status(self.parent_folder, self.get_container_program_id())

            if status != None:
                fraction, elapsed = status

                if self.container_data.render_data.do_video_render == True:
                    msg = _("Video for: ") + self.clip.name 
                #elif step == "2":
                #    msg = _("Image Sequence for: ") + self.clip.name 

                job_msg = self.get_job_queue_message()
                job_msg.progress = float(fraction)
                job_msg.elapsed = float(elapsed)
                job_msg.text = msg
                
                jobs.update_job_queue(job_msg)
            else:
                pass # This can happen sometimes before gmicheadless.py has written a status message, we just do nothing here.

    def create_icon(self):
        return self._create_icon_default_action()

    def abort_render(self):
        mltxmlheadless.abort_render(self.parent_folder, self.get_container_program_id())


# -------------------------------------------------------------- creating unrendered clip
def create_unrendered_clip(length, image_file, data, callback, window_text):
    unrendered_creation_thread = UnrenderedCreationThread(length, image_file, data, callback, window_text)
    unrendered_creation_thread.start()


# Creates a set length video clip from image to act as container clip unrendered media.
class UnrenderedCreationThread(threading.Thread):
    
    def __init__(self, length, image_file, data, callback, window_text):
        self.length = length
        self.image_file = image_file
        self.data = data
        self.callback = callback
        self.window_text = window_text

        threading.Thread.__init__(self)
        
    def run(self):
        # Image produceer
        img_producer = current_sequence().create_file_producer_clip(str(self.image_file)) # , new_clip_name=None, novalidate=False, ttl=None):

        # Create tractor to get right length.
        tractor = renderconsumer.get_producer_as_tractor(img_producer, self.length)
    
        # Consumer
        write_file = userfolders.get_cache_dir() + "/unrendered_clip.mp4"
        # Delete earlier created files
        if os.path.exists(write_file):
            os.remove(write_file)
        consumer = renderconsumer.get_default_render_consumer(write_file, PROJECT().profile)
        
        clip_renderer = renderconsumer.FileRenderPlayer(write_file, tractor, consumer, 0, self.length)
        clip_renderer.wait_for_producer_end_stop = True
        clip_renderer.start()

        GLib.idle_add(self._show_progress_window,  clip_renderer)
    
        while clip_renderer.stopped == False:       
            time.sleep(0.5)

        GLib.idle_add(self._do_write_callback, write_file)

    def _show_progress_window(self, clip_renderer):
        
        info_text = _("<b>Rendering Placeholder Media For:</b> ")  + self.data.get_program_name()

        progress_bar = Gtk.ProgressBar()
        self.dialog = rendergui.clip_render_progress_dialog(None, self.window_text, info_text, progress_bar, gui.editor_window.window, True)

        motion_progress_update = guiutils.ProgressWindowThread(self.dialog, progress_bar, clip_renderer, self.progress_thread_complete)
        motion_progress_update.start()
        
    def _do_write_callback(self, write_file):
        self.callback(write_file, self.data)

    def progress_thread_complete(self, dialog, some_number):
        GLib.idle_add(dialogutils.dialog_destroy, self.dialog, None)


class CommandLauncherThread(threading.Thread):
    def __init__(self, path, args):
        self.path = path
        self.args = args

        threading.Thread.__init__(self)

    def run(self):    
        # Create command list and launch process.
        command_list = [sys.executable]
        command_list.append(self.path)
        for arg in self.args:
            command_list.append(arg)

        subprocess.Popen(command_list)
        