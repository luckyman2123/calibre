#!/usr/bin/env python
# vim:fileencoding=utf-8
# License: GPL v3 Copyright: 2020, Kovid Goyal <kovid at kovidgoyal.net>


from threading import Thread

from calibre import prepare_string_for_xml

from .common import Event, EventType


class Client:

    mark_template = '<bookmark mark="{}"/>'
    name = 'sapi'

    @classmethod
    def escape_marked_text(cls, text):
        return prepare_string_for_xml(text)

    def __init__(self, settings=None, dispatch_on_main_thread=lambda f: f()):
        self.create_voice()
        self.ignore_next_stop_event = self.ignore_next_start_event = False
        self.default_system_rate = self.sp_voice.get_current_rate()
        self.default_system_voice = self.sp_voice.get_current_voice()
        self.default_system_sound_output = self.sp_voice.get_current_sound_output()
        self.current_stream_number = None
        self.current_callback = None
        self.dispatch_on_main_thread = dispatch_on_main_thread
        self.current_marked_text = self.last_mark = None
        self.status = {'synthesizing': False, 'paused': False}
        self.apply_settings(settings)

    def create_voice(self):
        from calibre.utils.windows.winsapi import ISpVoice
        self.sp_voice = ISpVoice()
        self.events_thread = Thread(name='SAPIEvents', target=self.wait_for_events, daemon=True)
        self.events_thread.start()

    def __del__(self):
        if self.sp_voice is not None:
            self.sp_voice.shutdown_event_loop()
            self.events_thread.join(5)
            self.sp_voice = None
    shutdown = __del__

    def apply_settings(self, new_settings=None):
        if self.status['paused']:
            self.sp_voice.resume()
            self.ignore_next_stop_event = True
            self.status = {'synthesizing': False, 'paused': False}
        settings = new_settings or {}
        self.sp_voice.set_current_rate(settings.get('rate', self.default_system_rate))
        self.sp_voice.set_current_voice(settings.get('voice') or self.default_system_voice)
        self.sp_voice.set_current_sound_output(settings.get('sound_output') or self.default_system_sound_output)

    def wait_for_events(self):
        while True:
            if self.sp_voice.wait_for_event() is False:
                break
            self.dispatch_on_main_thread(self.handle_events)

    def handle_events(self):
        from calibre_extensions.winsapi import (
            SPEI_END_INPUT_STREAM, SPEI_START_INPUT_STREAM, SPEI_TTS_BOOKMARK
        )
        c = self.current_callback
        for (stream_number, event_type, event_data) in self.sp_voice.get_events():
            if event_type == SPEI_TTS_BOOKMARK:
                self.last_mark = event_data
                event = Event(EventType.mark, event_data)
            elif event_type == SPEI_START_INPUT_STREAM:
                if self.ignore_next_start_event:
                    self.ignore_next_start_event = False
                    continue
                event = Event(EventType.begin)
                self.status = {'synthesizing': True, 'paused': False}
            elif event_type == SPEI_END_INPUT_STREAM:
                if self.ignore_next_stop_event:
                    self.ignore_next_stop_event = False
                    continue
                event = Event(EventType.end)
                self.status = {'synthesizing': False, 'paused': False}
            else:
                continue
            if c is not None and stream_number == self.current_stream_number:
                try:
                    c(event)
                except Exception:
                    import traceback
                    traceback.print_exc()

    def speak_simple_text(self, text):
        from calibre_extensions.winsapi import (
            SPF_ASYNC, SPF_IS_NOT_XML, SPF_PURGEBEFORESPEAK
        )
        self.current_callback = None
        self.current_marked_text = self.last_mark = None
        self.current_stream_number = self.sp_voice.speak(text, SPF_ASYNC | SPF_PURGEBEFORESPEAK | SPF_IS_NOT_XML, True)

    def speak_xml(self, text):
        from calibre_extensions.winsapi import (
            SPF_ASYNC, SPF_IS_XML, SPF_PURGEBEFORESPEAK
        )
        self.current_stream_number = self.sp_voice.speak(text, SPF_ASYNC | SPF_PURGEBEFORESPEAK | SPF_IS_XML, True)

    def speak_marked_text(self, text, callback):
        self.current_marked_text = text
        self.last_mark = None
        self.current_callback = callback
        self.speak_xml(text)

    def stop(self):
        from calibre_extensions.winsapi import SPF_PURGEBEFORESPEAK
        if self.status['paused']:
            self.sp_voice.resume()
        self.sp_voice.speak('', SPF_PURGEBEFORESPEAK, False)
        self.status = {'synthesizing': False, 'paused': False}
        if self.current_callback is not None:
            self.current_callback(Event(EventType.cancel))
        self.current_callback = None

    def pause(self):
        if self.status['synthesizing'] and not self.status['paused']:
            self.sp_voice.pause()
            self.status = {'synthesizing': True, 'paused': True}
            if self.current_callback is not None:
                self.current_callback(Event(EventType.pause))

    def resume(self):
        if self.status['paused']:
            self.sp_voice.resume()
            self.status = {'synthesizing': True, 'paused': False}
            if self.current_callback is not None:
                self.current_callback(Event(EventType.resume))

    def resume_after_configure(self):
        if self.status['paused']:
            self.resume()
        else:
            mark = self.mark_template.format(self.last_mark)
            idx = self.current_marked_text.find(mark)
            if idx == -1:
                text = self.current_marked_text
            else:
                text = self.current_marked_text[idx:]
            self.ignore_next_start_event = True
            if self.current_callback is not None:
                self.current_callback(Event(EventType.resume))
            self.speak_xml(text)
            self.status = {'synthesizing': True, 'paused': False}

    def get_voice_data(self):
        ans = getattr(self, 'voice_data', None)
        if ans is None:
            ans = self.voice_data = self.sp_voice.get_all_voices()
        return ans

    def get_sound_outputs(self):
        ans = getattr(self, 'sound_outputs', None)
        if ans is None:
            ans = self.sound_outputs = self.sp_voice.get_all_sound_outputs()
        return ans

    def config_widget(self, backend_settings, parent):
        from calibre.gui2.tts.windows_config import Widget
        return Widget(self, backend_settings, parent)
