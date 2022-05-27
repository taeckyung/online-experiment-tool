from pynput import mouse, keyboard
import cv2

from multiprocessing import Event, SimpleQueue, Value
from threading import Thread
import traceback
import signal
import time
import sys
import os

from typing import *

from utils import sound


def get_resource(name):
    if sys.platform == "darwin":
        return name
    else:
        return "./resources/" + name


class VideoRecorder(Thread):
    def __init__(self, base_path:str,  cam: int):
        super().__init__()
        self.event = Event()
        self.proceed_event = Event()
        self.cam = cam
        self.video_timeline = None
        self.video_cap = None
        self.video_out = None
        self.val = Value('i', 0, lock=True)
        self.base_path = base_path
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def signal_handler(self, sig, frame):
        if sig == signal.SIGINT:
            traceback.print_stack(frame)
            print("SIGINT FROM CHILD!", flush=True)

        self.output.close()
        if self.video_cap is not None:
            self.video_out.release()

        self.event.set()
        sys.exit(0)

    def execute(self):
        self.event.set()
        self.proceed_event.wait()

    def finish(self, timeout=None):
        self.event.clear()
        self.event.wait(timeout=timeout)

    def setFrameCount(self):
        with self.val.get_lock():
            self.val.value = 0

    def getFrameCount(self):
        return self.val.value

    def run(self) -> None:
        self.output = open(os.path.join(self.base_path, "video_timeline.txt"), 'w', buffering=1, encoding='UTF-8')
        if sys.platform == "darwin":
            self.video_cap = cv2.VideoCapture(self.cam)
        else:
            self.video_cap = cv2.VideoCapture(self.cam, cv2.CAP_DSHOW)
        self.video_cap.set(cv2.CAP_PROP_FPS, 30)

        size = (int(self.video_cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(self.video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))

        assert (self.video_cap.isOpened())

        fourcc = cv2.VideoWriter_fourcc(*'mpeg')
        self.video_out = cv2.VideoWriter(os.path.join(self.base_path, "recording.mp4"), fourcc, 30.0, size)
        self.event.wait()
        self.proceed_event.set()
        while self.event.is_set():
            ret, frame = self.video_cap.read()
            curr_time = time.time()
            if ret and frame is not None:
                self.video_out.write(frame)
                self.output.write("%f\n" % curr_time)
                with self.val.get_lock():
                    self.val.value += 1
        self.output.write("%f,end" % time.time())
        self.video_out.release()
        cv2.destroyAllWindows()
        self.output.close()
        self.event.set()


class ActivityRecorder(Thread):
    def __init__(self, base_path: str, queue: SimpleQueue, name: str):
        super().__init__()
        self.event = Event()
        self.finishEvent = Event()
        self.queue = queue
        self.name = name
        self.base_path = base_path
        self.mouse_listener = mouse.Listener(
            on_move=self.onMouseMove,
            on_click=self.onMouseClick,
            on_scroll=self.onMouseScroll
        )
        self.keyboard_listener = keyboard.Listener(
            on_press=self.onKeyPress,
            on_release=self.onKeyRelease
        )
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def signal_handler(self, sig, frame):
        try:
            self.keyboard_listener.stop()
            self.mouse_listener.stop()
        except Exception as e:
            print(e)

        try:
            self.keyboard_output.close()
            self.mouse_output.close()
        except Exception as e:
            print(e)

        if sig == signal.SIGINT:
            traceback.print_stack(frame)
            print("SIGINT FROM CHILD!", flush=True)

        sys.exit(0)

    def execute(self):
        self.event.set()

    def finish(self, timeout=None):
        self.event.set()
        self.finishEvent.wait(timeout=timeout)

    def key_log(self, string: str):
        self.keyboard_output.write("%f,%s\n" % (time.time(), string))

    def mouse_log(self, string: str):
        self.mouse_output.write("%f,%s\n" % (time.time(), string))

    def onMouseMove(self, x, y):
        self.mouse_log("mouse,move,%d,%d" % (x, y))

    def onMouseClick(self, x, y, button, pressed):
        self.mouse_log("mouse,click,%s,%d,%d,%d" % (button, pressed, x, y))

    def onMouseScroll(self, x, y, dx, dy):
        self.mouse_log("mouse,scroll,%d,%d,%d,%d" % (x, y, dx, dy))

    def onKeyPress(self, key):
        if isinstance(key, keyboard.KeyCode):
            if key in [keyboard.KeyCode.from_char('f'), keyboard.KeyCode.from_char('F'),
                       keyboard.KeyCode.from_char('ㄹ'),
                       keyboard.KeyCode.from_char('n'), keyboard.KeyCode.from_char('N'),
                       keyboard.KeyCode.from_char('ㅜ')]:
                self.key_log("key,press,%s" % str(key))

    def onKeyRelease(self, key):
        curr_time = time.time()
        if isinstance(key, keyboard.KeyCode):
            if key in [keyboard.KeyCode.from_char('f'), keyboard.KeyCode.from_char('F'),
                       keyboard.KeyCode.from_char('ㄹ')]:
                self.queue.put((curr_time, 'y'))
                sound.play(get_resource("Keyboard.mp3"))
                self.key_log("key,release,%s" % str(key))
            elif key in [keyboard.KeyCode.from_char('n'), keyboard.KeyCode.from_char('N'),
                         keyboard.KeyCode.from_char('ㅜ')]:
                self.queue.put((curr_time, 'n'))
                sound.play(get_resource("Keyboard.mp3"))
                self.key_log("key,release,%s" % str(key))

    def run(self) -> None:
        self.mouse_output = open(os.path.join(self.base_path, "mouse_log_%s.txt" % self.name), 'w', buffering=1,
                                 encoding='UTF-8')
        self.keyboard_output = open(os.path.join(self.base_path, "keyboard_log_%s.txt" % self.name), 'w', buffering=1,
                                    encoding='UTF-8')
        self.event.wait()

        self.mouse_listener.start()
        self.keyboard_listener.start()

        self.event.clear()
        self.event.wait()

        self.mouse_listener.stop()
        self.keyboard_listener.stop()

        self.mouse_output.close()
        self.keyboard_output.close()

        self.finishEvent.set()
