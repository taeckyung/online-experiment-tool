from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *
import dlib
import cv2

from multiprocessing import Event, freeze_support, SimpleQueue
from imutils import face_utils
from threading import Thread
from enum import Enum, auto
import functools
import traceback
import imutils
import shutil
import signal
import time
import sys
import os

from typing import List, Tuple

from utils import *


def get_time(time_now, total):
    return time_now//60, time_now % 60, total//60, total % 60


class ProbeRunner(QThread):
    signal = pyqtSignal()
    ui_signal = pyqtSignal()

    def __init__(self, queue: SimpleQueue, name: str):
        super().__init__()
        self.event = Event()
        self.end_event = Event()
        self.queue = queue
        self.name = name

    def execute(self):
        self.event.set()

    def finish(self, timeout=None):
        self.event.clear()
        self.end_event.wait(timeout=timeout)

    def run(self) -> None:
        self.event.wait()

        max_response = 10  # s

        clock_before = 0
        idx_before = 0

        output_str = ""
        last_probe = None
        added = True

        while True:
            clock_now = time.time()

            # Play ding sound
            if (clock_now % 40) < 5. and (clock_now - clock_before) > 20.:
                sound.play(get_resource("Ding-sound-effect.mp3"))
                output_str += "%f,sound\n" % clock_now
                idx_before += 1
                clock_before = clock_now
                last_probe = None
                added = False

            # Give 10 seconds padding for report
            if (clock_now - clock_before) > 10:
                while not self.queue.empty():
                    e: Tuple[float, str] = self.queue.get()
                    if 0. <= e[0] - clock_before < max_response:
                        if last_probe is not None and last_probe[1] != e[1]:  # drop when user type different responses
                            last_probe = None
                        else:
                            last_probe = e
                if last_probe is not None and not added:
                    output_str += "%f,%f,probe,%s\n" % (clock_before, last_probe[0], last_probe[1])
                    added = True

                    with open(os.path.join(BASE_PATH, "probe_%s.txt" % self.name), 'w', encoding='UTF-8') as f:
                        f.write(output_str)


def proceedFunction(state_before, state_after):
    """
    These functions will call function `proceed`.

    :param state_after:
    :param state_before:
    :return:
    """
    def proceedFunction(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            assert(self._state == state_before or self._state in state_before)
            func(self, *args, **kwargs)
            if state_after is not None:
                self._state = state_after
                self.proceed()
        return wrapper
    return proceedFunction


class ExpApp(QMainWindow):

    class ProbingDialog(QDialog):
        def __init__(self, probe_text, close_dialog):
            super().__init__()
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

            dialog_layout = QVBoxLayout(self)
            self.label = QLabel("Did you hear the beep sound?\n"
                               "After hearing the sound, you should response your attentional state!\n\n"
                               + probe_text + "\n\n"
                               "You can check this guide below.")
            self.label.setAlignment(Qt.AlignCenter)
            self.button = QPushButton("OK")

            dialog_layout.addStretch(1)
            dialog_layout.addWidget(self.label, alignment=Qt.AlignVCenter)
            dialog_layout.addStretch(1)
            dialog_layout.addWidget(self.button, alignment=Qt.AlignVCenter)
            dialog_layout.addStretch(1)
            self.setLayout(dialog_layout)
            self.setWindowTitle('Alert')
            self.setWindowModality(Qt.ApplicationModal)
            self.closeDialog = close_dialog

        def connect(self, f):
            self.button.clicked.connect(f)

        def closeEvent(self, a0: QCloseEvent) -> None:
            self.closeDialog()
            return super().closeEvent(a0)

    class State(Enum):
        START = auto()
        SET_DISTRACTION = auto()
        SET_PARAMETERS = auto()
        CALIB_INSTRUCTION = auto()
        SET_CAMERA = auto()
        CALIBRATION = auto()
        LECTURE_INSTRUCTION = auto()
        DEMO_VIDEO = auto()
        MAIN_VIDEO = auto()
        WAITING = auto()
        FINISH = auto()
        ERROR = auto()

    _state = State.START

    def signal_handler(self, sig, frame):
        if sig == signal.SIGINT:
            traceback.print_stack(frame)
        self.close()

    def log(self, string: str):
        print(string, flush=True)
        self.output.write("%f,%s,%s\n" % (time.time(), self._state, string))

    @pyqtSlot("QWidget*", "QWidget*")
    def onFocusChanged(self, old, now):
        if now is None:
            self.log("focus,False")
        else:
            self.log("focus,True")

    def closeEvent(self, event):
        self.log("click,x")
        self.close()

    def close(self):
        try:
            self.media_player.stop()
        except Exception as e:
            self.log(str(e))

        try:
            self.probeRunner.finish(timeout=5.0)
            self.probeRunner.terminate()
            self.updater.finish(timeout=1.0)
            self.updater.terminate()
        except Exception as e:
            self.log(str(e))

        try:
            self.videoRecorder.finish(timeout=5.0)
            self.activityRecorder.finish(timeout=5.0)
        except Exception as e:
            self.log(str(e))

        try:
            self.probeRunner.finish(timeout=5.0)
            self.probeRunner.terminate()
        except Exception as e:
            self.log(str(e))

        try:
            self.videoRecorder.finish(timeout=10.0)
            self.videoRecorder.join()
            #self.videoRecorder.join(timeout=2.0)
            #self.videoRecorder.terminate()
        except Exception as e:
            self.log(str(e))

        try:
            self.activityRecorder.finish(timeout=3.0)
            self.activityRecorder.join()
            #self.activityRecorder.join(timeout=2.0)
            #self.activityRecorder.terminate()
        except Exception as e:
            self.log(str(e))

        try:
            if sys.platform == "darwin":
                output_name = os.path.join("../../../", "output_user_%s" % self.user_id.text())
                save_idx = 0
                while os.path.isfile(output_name+".zip"):
                    save_idx += 1
                    output_name = output_name.split("(")[0] + ("(%d)" % save_idx)
                shutil.make_archive(output_name, 'zip', "./%s/"%BASE_PATH)
            else:
                output_name = os.path.join("./", "output_user_%s" % self.user_id.text())
                save_idx = 0
                while os.path.isfile(output_name+".zip"):
                    save_idx += 1
                    output_name = output_name.split("(")[0] + ("(%d)" % save_idx)
                shutil.make_archive(output_name, 'zip', "./%s/"%BASE_PATH)
        except Exception as e:
            self.log(str(e))

        self.output.close()

        # os.system("start https://forms.gle/1111")
        # taskbar.unhide_taskbar()
        sys.exit(0)

    def __init__(self, *args, **kwargs):
        QMainWindow.__init__(self, *args, **kwargs)

        # taskbar.hide_taskbar()

        # Debugging options (Disable camera setting & calibration)
        self._skip_camera = True
        self._skip_calib = True

        ########### MODIFY HERE! ######################################
        self.videos = []
        ###############################################################

        self.videoIndex = 0

        self.output = open(os.path.join(BASE_PATH, "main_log.txt"), 'w', buffering=1, encoding='UTF-8')
        self.camera = camera.select_camera(os.path.join(BASE_PATH, "test.png"))
        if self.camera is None:
            self.log("cameraNotFound")

        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

        self.small_font = QFont("Roboto")
        self.small_font.setPixelSize(13)
        self.small_font.setBold(False)

        # initVLC
        if True:
            """
            https://github.com/devos50/vlc-pyqt5-example
            :return:
            """
            self.instance = vlc.Instance()
            self.media_player: vlc.MediaPlayer = None

        # initChild
        if True:
            self.probeQueue = SimpleQueue()

            self.probeRunner = None
            self.updater = None

            self.videoRecorder = None

            self.activityRecorder = ActivityRecorder(BASE_PATH, self.probeQueue, "Main")
            self.activityRecorder.daemon = True
            self.activityRecorder.start()
            self.activityRecorder.execute()  # Start recording keyboard & mouse

        # initUI
        if True:
            self.setWindowTitle('Online Experiment Application')
            #self.setWindowIcon(QIcon('resources/nmsl_logo_yellow.png'))
            self.setWindowIcon(QIcon(get_resource('nmsl_logo_yellow.png')))

            self.widget = QStackedWidget(self)
            self.distraction_instruction_widget = QWidget(self)
            self.calib_instruction_widget = QWidget(self)
            self.camera_setting_widget = QWidget(self)
            self.calibration_widget = QWidget(self)
            self.lecture_instruction_widget = QWidget(self)
            self.lecture_video_widget = QWidget(self)
            self.waiting_widget = QWidget(self)
            self.finish_widget = QWidget(self)

            self.widget.addWidget(self.distraction_instruction_widget)
            self.widget.addWidget(self.calib_instruction_widget)
            self.widget.addWidget(self.camera_setting_widget)
            self.widget.addWidget(self.calibration_widget)
            self.widget.addWidget(self.lecture_instruction_widget)
            self.widget.addWidget(self.lecture_video_widget)
            self.widget.addWidget(self.waiting_widget)
            self.widget.addWidget(self.finish_widget)

            self.setCentralWidget(self.widget)
            self.widget.setCurrentWidget(self.calib_instruction_widget)

            # Set Camera Setting Screen
            if True:
                camera_layout = QVBoxLayout(self)
                camera_text = QLabel(
                    'Please move your monitor/laptop close and center your face so it exceeds BLUE rectangle.\n\n'
                    'Please avoid direct lights into the camera.'
                    , self
                )
                camera_text.setAlignment(Qt.AlignCenter)
                camera_text.setFixedHeight(100)

                self.camera_label = QLabel(self)
                self.camera_label.setAlignment(Qt.AlignCenter)

                self.camera_finish_button = QPushButton("Next", self)
                self.camera_finish_button.setFixedHeight(50)

                camera_layout.addWidget(camera_text, alignment=Qt.AlignVCenter)
                camera_layout.addWidget(self.camera_label, alignment=Qt.AlignVCenter)
                camera_layout.addWidget(self.camera_finish_button, alignment=Qt.AlignVCenter)

                self.camera_running = Event()

                self.camera_setting_widget.setLayout(camera_layout)

            # Set Notification Widget
            if True:
                notification_layout = QVBoxLayout(self)

                noti_text = QLabel(
                    'Thank you for your participation in the project.\n'
                    'Your participation will help to improve the understanding of online learning.\n'
                    'Please make sure you completed pre-experiment survey and pre-quiz.\n\n'
                    'Please disable every external distractions:\n'
                    '- Mute your phone, tablet, etc.\n'
                    '- Disable notifications from Messenger programs (Slack, KakaoTalk, etc.)\n'
                    '- Disconnect every external monitor (if you are connected)\n'
                    '- Please do not let others disturb you\n'
                    '- (Mac) In the control center, click 🌙 (moon) icon, and turn on do-not-disturb mode for at least 2 hours.\n'
                    '- (Windows) Disable notification as below image'
                    , self
                )
                noti_text.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
                noti_text.setContentsMargins(10, 10, 10, 10)

                noti_image = QLabel(self)
                noti_image.setFixedSize(758, 270)
                #noti_image.setPixmap(QPixmap("./resources/focus_assistant.png"))
                noti_image.setPixmap(QPixmap(get_resource("focus_assistant.png")))

                noti_open = QPushButton('Open Settings (Only Windows)')
                noti_open.setFixedSize(758, 50)
                noti_open.clicked.connect(notification.open_settings)

                type_student_id_text = QLabel('Type your Name below.')
                type_student_id_text.setFixedHeight(75)

                self.user_id = QLineEdit(self)
                self.user_id.setFixedSize(758, 50)
                self.user_id.setAlignment(Qt.AlignCenter)
                self.user_id.setValidator(QRegExpValidator(QRegExp("[A-Za-z0-9]+")))  # QIntValidator()

                self.noti_proceed = QPushButton('Next')
                self.noti_proceed.setFixedSize(758, 50)
                self.noti_proceed.clicked.connect(self.proceed)

                notification_layout.addStretch(10)
                notification_layout.addWidget(noti_text, alignment=Qt.AlignHCenter)
                notification_layout.addStretch(1)
                notification_layout.addWidget(noti_image, alignment=Qt.AlignHCenter)
                notification_layout.addStretch(3)
                notification_layout.addWidget(noti_open, alignment=Qt.AlignHCenter)
                notification_layout.addStretch(3)
                notification_layout.addWidget(type_student_id_text, alignment=Qt.AlignHCenter)
                notification_layout.addStretch(1)
                notification_layout.addWidget(self.user_id, alignment=Qt.AlignHCenter)
                notification_layout.addStretch(1)
                notification_layout.addWidget(self.noti_proceed, alignment=Qt.AlignHCenter)
                notification_layout.addStretch(10)

                notification_layout.setSpacing(10)
                self.distraction_instruction_widget.setLayout(notification_layout)

            # Set Calibration Instruction Widget
            if True:
                instruction_layout = QVBoxLayout(self)

                detail_text = QLabel(
                    'Now, you will proceed an loop of "Looking at a circle" -> "Clicking the circle".\n\n'
                    '- Please do not move your head during the step.\n\n'
                    '- You may need to click multiples times.',
                    self
                )
                detail_text.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
                detail_text.setContentsMargins(10, 10, 10, 10)

                self.start_calib_button = QPushButton('Next', self)
                self.start_calib_button.setFixedSize(758, 50)

                instruction_layout.addStretch(10)
                instruction_layout.addWidget(detail_text, 0, alignment=Qt.AlignHCenter)
                instruction_layout.addStretch(1)
                instruction_layout.addWidget(self.start_calib_button, 0, alignment=Qt.AlignHCenter)
                instruction_layout.addStretch(10)

                self.calib_instruction_widget.setLayout(instruction_layout)

            # Set Calibration Widget
            if True:
                calib_layout = QVBoxLayout(self)
                # Calibration button
                self.ellipse_button = QPushButton('', self)
                self.ellipse_button.move(0, 0)
                self.ellipse_button.setStyleSheet("background-color: transparent")
                self.ellipse_button.hide()
                self.ellipse_button.clicked.connect(self.proceed)

                calib_layout.addWidget(self.ellipse_button, alignment=Qt.AlignAbsolute)
                self.calibration_widget.setLayout(calib_layout)

            # Set Lecture Instruction Widget
            if True:
                instruction_layout = QVBoxLayout(self)

                lecture_text = QLabel(
                    '-----------------------------------------IMPORTANT-----------------------------------------\n\n'
                    'During the experiment, please avoid moving laptop or touching eyeglasses.\n\n'
                    'During the lecture, you will periodically hear the "beep" sound.\n\n'
                    'When you hear the sound, based on your state JUST BEFORE hearing the sound:\n\n'
                    '- Press [F]: if you were Focusing (thinking of anything related to the lecture)\n\n'
                    '- Press [N]: if you were NOT focusing (thinking or doing something unrelated to the lecture)\n\n'
                    '- DO NOT PRESS: if you cannot decide\n\n'
                    'If you pressed the wrong key, then just press again.\n\n\n'
                    '----------------------------------------------------------------------------------------------------\n\n'
                    'Please adjust your system volume to make sure you hear the beep sound.\n\n'
                    'Proceed when you are ready.',
                    self
                )
                lecture_text.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
                lecture_text.setContentsMargins(10, 10, 10, 10)

                beep_button = QPushButton('Test Beep Sound', self)
                beep_button.setFixedSize(758, 50)

                def launch_beep():
                    #sound.play("./resources/Ding-sound-effect.mp3")
                    sound.play(get_resource("Ding-sound-effect.mp3"))
                    
                beep_button.clicked.connect(launch_beep)

                start_button = QPushButton('Next', self)
                start_button.setFixedSize(758, 50)
                start_button.clicked.connect(self.proceed)

                instruction_layout.addStretch(10)
                instruction_layout.addWidget(lecture_text, 0, alignment=Qt.AlignHCenter)
                instruction_layout.addStretch(1)
                instruction_layout.addWidget(beep_button, 0, alignment=Qt.AlignHCenter)
                instruction_layout.addStretch(1)
                instruction_layout.addWidget(start_button, 0, alignment=Qt.AlignHCenter)
                instruction_layout.addStretch(10)

                self.lecture_instruction_widget.setLayout(instruction_layout)

            # Set Lecture Video Widget
            if True:
                vlc_layout = QVBoxLayout(self)
                # VLC player
                # In this widget, the video will be drawn
                self.video_frame = QFrame()

                palette = self.video_frame.palette()
                palette.setColor(QPalette.Window, QColor(255, 255, 255))
                self.video_frame.setPalette(palette)
                self.video_frame.setAutoFillBackground(True)
                vlc_layout.addWidget(self.video_frame, alignment=Qt.AlignVCenter)
                #vlc_layout.updateGeometry()
                #self.video_frame.updateGeometry()

                # Lower Layout ###################################################################
                vlc_lower_layout = QHBoxLayout(self)

                vlc_lower_layout.addStretch(1)

                self.next_button = QPushButton('Start Video', self)
                self.next_button.setFixedSize(100, 30)
                self.next_button.clicked.connect(self.proceed)
                self.next_button.setFont(self.small_font)
                vlc_lower_layout.addWidget(self.next_button, alignment=Qt.AlignHCenter)

                self.video_index_text = ' [Video: %01d/%01d] '
                self.video_index_label = QLabel(self.video_index_text % (1, len(self.videos)))
                self.video_index_label.setFixedSize(80, 30)
                self.video_index_label.setFont(self.small_font)
                vlc_lower_layout.addWidget(self.video_index_label, alignment=Qt.AlignHCenter)

                self.time_text = ' [Time: %02d:%02d/%02d:%02d] '
                self.time_label = QLabel(self.time_text % (0, 0, 0, 0))
                self.time_label.setFixedSize(160, 30)
                self.time_label.setFont(self.small_font)
                vlc_lower_layout.addWidget(self.time_label, alignment=Qt.AlignHCenter)

                vlc_lower_layout.addStretch(1)

                self.probe_text = 'FOCUSED: [F] / NOT FOCUSED: [N] / SKIP: [Space]'
                self.probe_label = QLabel(self.probe_text)
                font: QFont = self.probe_label.font()
                font.setFamily('Roboto')
                font.setPixelSize(13)
                font.setBold(True)
                self.probe_label.setFont(font)
                self.probe_label.setFixedSize(500, 30)
                vlc_lower_layout.addWidget(self.probe_label, alignment=Qt.AlignHCenter)

                vlc_lower_layout.addStretch(1)

                # Volume Layout ####################################################
                vlc_volume_layout = QHBoxLayout(self)
                vlc_volume_layout.setSpacing(1)

                vlc_volume_layout.addStretch(1)

                volume_label = QLabel('Volume:')
                volume_label.setFixedSize(50, 30)
                volume_label.setFont(self.small_font)
                vlc_volume_layout.addWidget(volume_label, alignment=Qt.AlignHCenter | Qt.AlignRight)

                volume_slider = QSlider(Qt.Horizontal, self)
                volume_slider.setMaximum(100)
                volume_slider.setMaximumWidth(300)
                volume_slider.setFixedHeight(25)
                volume_slider.setValue(100)
                volume_slider.setToolTip("Volume")
                volume_slider.valueChanged.connect(self.setVolume)

                vlc_volume_layout.addWidget(volume_slider, alignment=Qt.AlignHCenter)
                vlc_volume_layout.addStretch(1)

                vlc_lower_layout.addLayout(vlc_volume_layout)
                #####################################################################

                vlc_lower_layout.addStretch(1)
                vlc_layout.addLayout(vlc_lower_layout)
                #################################################################################

                vlc_layout.setSpacing(0)
                vlc_layout.setContentsMargins(0, 0, 0, 0)
                self.lecture_video_widget.setLayout(vlc_layout)
                # Dialog
                self.dialog = self.ProbingDialog(self.probe_text, self.closeDialog)
                self.dialog.connect(self.closeDialog)


            # Waiting scene
            if True:
                waiting_layout = QVBoxLayout(self)

                lecture_text = QLabel(
                    'From now, you can minimize the current screen.\n\n\n'
                    '-----------------------------------------IMPORTANT-----------------------------------------\n\n'
                    '- Press [F]: if you were Focusing (thinking of anything related to the lecture)\n\n'
                    '- Press [N]: if you were NOT focusing (thinking or doing something unrelated to the lecture)\n\n'
                    '- DO NOT PRESS: if you cannot decide\n\n'
                    'If you pressed the wrong key, then just press again.\n\n\n'
                    '------------------------------------------IF ENDS------------------------------------------\n\n'
                    'Please press the button AFTER the meeting ends!\n\n',
                    self
                )
                lecture_text.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
                lecture_text.setContentsMargins(10, 10, 10, 10)
                font: QFont = lecture_text.font()
                font.setFamily('Roboto')
                font.setPixelSize(20)
                font.setBold(True)
                lecture_text.setFont(font)

                waiting_layout.addWidget(lecture_text, alignment=Qt.AlignBottom|Qt.AlignHCenter)
                waiting_layout.stretch(1)

                start_button = QPushButton('PRESS WHEN MEETING ENDS', self)
                start_button.setFixedSize(758, 50)
                start_button.clicked.connect(self.proceed)
                waiting_layout.addWidget(start_button, alignment=Qt.AlignTop|Qt.AlignHCenter)
                waiting_layout.stretch(1)

                self.waiting_widget.setLayout(waiting_layout)


            # Finish scene
            if True:
                finish_layout = QVBoxLayout(self)
                self.finish_text = 'Thank you for the participation!\n'\
                                   'Please do not forget to submit the result :)\n\n'
                self.finish_label = QLabel(self.finish_text)
                self.finish_label.setAlignment(Qt.AlignCenter)
                self.finish_label.setFixedHeight(200)
                font: QFont = self.finish_label.font()
                font.setBold(True)
                font.setPixelSize(30)
                self.finish_label.setFont(font)
                finish_layout.addWidget(self.finish_label, alignment=Qt.AlignHCenter)

                finish_button = QPushButton('Finish\n(Please Wait)', self)
                finish_button.setFixedSize(758, 100)
                finish_button.clicked.connect(self.close)
                finish_layout.addWidget(finish_button, alignment=Qt.AlignHCenter)

                self.finish_widget.setLayout(finish_layout)

            # Maximize the screen
            self.showMaximized()

            # Set focused and make on-focus checker
            qApp.focusChanged.connect(self.onFocusChanged)

            # Calibration parameters
            self.margin = 0
            self.calib_r = 50
            self.pos = 0
            self.clicks = 0
            self.calib_started = False
            self.calib_position_center: List[Tuple[int, int]] = [(0, 0)]

        self._state = self.State.SET_DISTRACTION
        self.widget.setCurrentWidget(self.distraction_instruction_widget)

    def proceed(self):
        """
        Every non-inherited methods are executed here.
        This function is only called at proceedFunction().

        :return:
        """
        self.log(f'proceed: {self._state}')
        if self._state is self.State.SET_DISTRACTION:
            self.set_notification()
        elif self._state is self.State.SET_PARAMETERS:
            if self.user_id.text() != "":
                self.initialize()
        elif self._state is self.State.CALIB_INSTRUCTION:
            self.set_instruction()
        elif self._state is self.State.SET_CAMERA:
            self.set_camera()
        elif self._state is self.State.CALIBRATION:
            self.calibrate()
        elif self._state is self.State.LECTURE_INSTRUCTION:
            self.lecture_instruction()
        elif self._state is self.State.DEMO_VIDEO:
            self.start_video(demo=True)
        elif self._state is self.State.MAIN_VIDEO:
            self.start_video()
        elif self._state is self.State.WAITING:
            self.waiting_function()
        elif self._state is self.State.FINISH:
            self.final()

    @proceedFunction(State.SET_DISTRACTION, State.SET_PARAMETERS)
    def set_notification(self):
        return

    @proceedFunction(State.SET_PARAMETERS, State.SET_CAMERA)
    def initialize(self):
        self.noti_proceed.setDisabled(True)

        screen = qApp.primaryScreen()
        dpi = screen.physicalDotsPerInch()
        full_screen = screen.size()
        # self.setFixedHeight(full_screen.height())
        # self.setFixedWidth(full_screen.width())

        x_mm = 2.54 * full_screen.height() / dpi  # in->cm
        y_mm = 2.54 * full_screen.width() / dpi  # in->cm
        self.calib_r = int(min(full_screen.width(), full_screen.height()) / 100)
        self.margin = self.calib_r * 2

        # Leave logs
        self.log('experimenter,%s' % self.user_id.text())
        self.log('monitor,%f,%f' % (x_mm, y_mm))
        self.log('resolution,%d,%d' % (full_screen.width(), full_screen.height()))
        self.log('inner_area,%d,%d' % (self.rect().width(), self.rect().height()))
        self.log('calibration_Radius,%d' % self.calib_r)

        # Resize frames
        self.camera_label.setFixedHeight(self.rect().height() - 200)
        self.video_frame.setFixedHeight(self.rect().height() - 30)

        # Sort URL w.r.t. Student ID
        # random.seed(self.user_id.text())
        # target = self.videos[1:]
        # random.shuffle(target)
        # self.videos[1:] = target
        self.log(f'videos,{self.videos}')

        self.ellipse_button.setFixedSize(self.calib_r * 2, self.calib_r * 2)
        self.ellipse_button.show()
        self.calib_position_center = [
            (self.margin, self.margin),
            (self.rect().width() / 2, self.margin),
            (self.rect().width() - self.margin, self.margin),

            (self.margin, self.rect().height() / 2),
            (self.rect().width() / 2, self.rect().height() / 2),
            (self.rect().width() - self.margin, self.rect().height() / 2),

            (self.margin, self.rect().height() - self.margin),
            (self.rect().width() / 2, self.rect().height() - self.margin),
            (self.rect().width() - self.margin, self.rect().height() - self.margin),

            (self.rect().width() / 4, self.rect().height() / 4),
            (self.rect().width() * 3 / 4, self.rect().height() / 4),
            (self.rect().width() / 4, self.rect().height() * 3 / 4),
            (self.rect().width() * 3 / 4, self.rect().height() * 3 / 4),

            (self.calib_r, self.calib_r),
            (self.rect().width() - self.calib_r, self.calib_r),
            (self.calib_r, self.rect().height() - self.calib_r),
            (self.rect().width() - self.calib_r, self.rect().height() - self.calib_r),
        ]

    @proceedFunction(State.SET_CAMERA, None)  # Next: CALIB_INSTRUCTION
    def set_camera(self):
        if not self._skip_camera:
            self.camera_finish_button.setDisabled(True)
        self.widget.setCurrentWidget(self.camera_setting_widget)

        cap = None
        success = self.camera is not None

        if success:
            if sys.platform=="darwin":
                cap = cv2.VideoCapture(self.camera)
            else:
                cap = cv2.VideoCapture(self.camera, cv2.CAP_DSHOW)
            cap.set(cv2.CAP_PROP_FPS, 30)

            if not cap.isOpened():
                success = False

        if not success:
            self.camera_finish_button.setText("Quit")
            def quick_exit():
                sys.exit(0)
            self.camera_finish_button.clicked.connect(quick_exit)
            self.camera_finish_button.setEnabled(True)
            self.log("setMonitor,fail")
            self.camera_label.setText("No Camera Detected.\n\nPlease check if\n"
                                      " - Camera is properly connected.\n"
                                      " - (Mac) You allowed camera permission.\n"
                                      " - (Windows) No other app is using camera.")
            return

        width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        self.log("cameraCapture,%d,%d" % (int(width), int(height)))

        def distance2(p1, p2):
            return (p1[0]-p2[0])**2 + (p1[1]-p2[1])**2

        def frame_thread_run(success: Event):
            detector = dlib.get_frontal_face_detector()
            while not self.camera_running.is_set():
                try:
                    ret, img = cap.read()
                    if ret:
                        img = img.copy()
                        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                        img = cv2.flip(img, 1)
                        img = imutils.resize(img, width=500)

                        # Detect face bounding box
                        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                        rect = detector(gray, 1)
                        h, w, c = img.shape
                        t_size = w/5  # target size
                        if len(rect) > 0:
                            (x, y, x_d, y_d) = face_utils.rect_to_bb(rect[0])
                            img = cv2.rectangle(img, (x, y), (x+x_d, y+y_d), (255, 0, 0), 2)
                            rect_center = (x+int(x_d/2), y+int(y_d/2))
                            img = cv2.circle(img, rect_center, 1, (255, 0, 0), -1)
                            if x_d >= t_size and distance2(rect_center, (int(w/2), int(h/2))) < t_size**2:
                                self.camera_finish_button.setEnabled(True)
                                success.set()

                        # Draw target box
                        img = cv2.rectangle(img, (int((w-t_size)/2), int((h-t_size)/2)),
                                            (int((w+t_size)/2), int((h+t_size)/2)), (0, 0, 255), 2)
                        img = cv2.circle(img, (int(w/2), int(h/2)), 1, (0, 0, 255), -1)
                        img = imutils.resize(img, height=int(self.camera_label.height()))

                        # Draw in PyQT
                        h, w, c = img.shape
                        image = QImage(img.data, w, h, w*c, QImage.Format_RGB888)
                        pixmap = QPixmap.fromImage(image)
                        self.camera_label.setPixmap(pixmap)
                    else:
                        break
                except Exception as e:
                    self.log(str(e))
                    break
        success = Event()
        frame_thread = Thread(target=frame_thread_run, args=(success,))
        frame_thread.daemon = True
        frame_thread.start()

        def camera_finished_wrapper():
            if success.is_set() or self._skip_camera:
                self.camera_finished(frame_thread, cap)

        self.camera_finish_button.clicked.connect(camera_finished_wrapper)

    @proceedFunction(State.SET_CAMERA, State.CALIB_INSTRUCTION)
    def camera_finished(self, frame_thread, cap):
        self.camera_running.set()

        frame_thread.join()
        cap.release()
        # Start recording
        self.videoRecorder = VideoRecorder(BASE_PATH, self.camera)
        #self.videoRecorder.video_cap = cap
        self.videoRecorder.daemon = True
        self.videoRecorder.start()
        self.videoRecorder.execute()

    @proceedFunction(State.CALIB_INSTRUCTION, None)  # Next: CALIBRATION
    def set_instruction(self):
        self.widget.setCurrentWidget(self.calib_instruction_widget)

        def set_instruction_finished_wrapper():
            self.set_instruction_finished()

        self.start_calib_button.clicked.connect(set_instruction_finished_wrapper)

    @proceedFunction(State.CALIB_INSTRUCTION, State.CALIBRATION)
    def set_instruction_finished(self):
        self.widget.setCurrentWidget(self.calibration_widget)

    def paintEvent(self, event):
        qp = QPainter(self)
        if self._state == self.State.CALIBRATION:
            if 0 <= self.pos < len(self.calib_position_center):
                qp.setBrush(QColor(180, 0, 0))
                qp.setPen(QPen(QColor(180, self.calib_r, self.calib_r), 1))
                x, y = self.calib_position_center[self.pos]
                r = self.calib_r
                qp.drawEllipse(x-r, y-r, 2*r, 2*r)
        qp.end()

    @proceedFunction(State.CALIBRATION, None)  # Next: LECTURE_INSTRUCTION
    def calibrate(self):
        self.log("calibrate,%d" % self.pos)

        if self._skip_calib:
            self.pos = len(self.calib_position_center) + 1
            self.ellipse_button.hide()
            self.end_calibrate()
            return
        if self.clicks == 0:  # First click on the point
            self.videoRecorder.setFrameCount()
            self.clicks += 1
        elif self.videoRecorder.getFrameCount() < 15:
            self.clicks += 1
        else:
            self.clicks = 0
            self.pos += 1
        if self.pos >= len(self.calib_position_center):
            self.ellipse_button.hide()
            self.end_calibrate()
            return
        self.ellipse_button.move(self.calib_position_center[self.pos][0] - self.calib_r,
                                 self.calib_position_center[self.pos][1] - self.calib_r)
        self.update()

    @proceedFunction(State.CALIBRATION, State.LECTURE_INSTRUCTION)
    def end_calibrate(self):
        return

    @proceedFunction(State.LECTURE_INSTRUCTION, None)  # Next: DEMO_VIDEO
    def lecture_instruction(self):
        self.widget.setCurrentWidget(self.lecture_instruction_widget)
        self._state = self.State.WAITING

    @proceedFunction(State.WAITING, None)
    def waiting_function(self):
        self.widget.setCurrentWidget(self.waiting_widget)
        self._state = self.State.FINISH

        self.activityRecorder.finish(timeout=5.0)  # Stop recording keyboard & mouse
        self.activityRecorder.join()

        self.activityRecorder = ActivityRecorder(BASE_PATH, self.probeQueue, "waiting")
        self.activityRecorder.daemon = True
        self.activityRecorder.start()
        self.activityRecorder.execute()

        self.probeRunner = ProbeRunner(self.probeQueue, "waiting")
        self.probeRunner.daemon = True
        self.probeRunner.start()
        self.probeRunner.execute()

    def showDialog(self):
        self.media_player.pause()
        self.dialog.show()

    def closeDialog(self):
        self.dialog.close()
        self.media_player.play()

    def getVolume(self):
        return self.media_player.audio_get_volume()

    def setVolume(self, vol):
        self.media_player.audio_set_volume(vol)

    @proceedFunction([State.DEMO_VIDEO, State.MAIN_VIDEO], None)
    def start_video(self, demo=False):
        pass

    def getScreenSize(self):
        if sys.platform == 'darwin':
            from AppKit import NSScreen, NSDeviceSize, NSDeviceResolution
            from Quartz import CGDisplayScreenSize
            screen = NSScreen.mainScreen()
            description = screen.deviceDescription()
            pw, ph = description[NSDeviceSize].sizeValue()
            rx, ry = description[NSDeviceResolution].sizeValue()
            mmw, mmh = CGDisplayScreenSize(description["NSScreenNumber"])
            scaleFactor = screen.backingScaleFactor()
            pw *= scaleFactor
            ph *= scaleFactor
            self.log(f"display: {mmw:.1f}×{mmh:.1f} mm; {pw:.0f}×{ph:.0f} pixels; {rx:.0f}×{ry:.0f} dpi")
            return pw, ph
        else:
            return self.rect().width(), self.rect().height()


    @proceedFunction(State.MAIN_VIDEO, State.FINISH)
    def finishVideo(self):
        self.probeRunner.finish(timeout=5.0)
        self.probeRunner.terminate()
        self.updater.terminate()
        return

    def final(self):
        self.finish_label.setText(self.finish_text)
        self.widget.setCurrentWidget(self.finish_widget)


if __name__ == '__main__':
    BASE_PATH = "output"
    idx = 0
    while True:
        if not os.path.exists(BASE_PATH):
            os.mkdir(BASE_PATH)
            break
        BASE_PATH = "output%d" % idx
        idx += 1

    with open(os.path.join(BASE_PATH, "stdout.txt"), 'w', buffering=1, encoding='UTF-8') as stdout:
        sys.stdout = stdout

        with open(os.path.join(BASE_PATH, "stderr.txt"), 'w', buffering=1, encoding='UTF-8') as stderr:
            sys.stderr = stderr

            # Pyinstaller fix
            freeze_support()

            # PyQT
            app = QApplication(sys.argv)
            font = QFont("Roboto")
            font.setBold(True)
            font.setPixelSize(20)
            app.setFont(font)
            ex = ExpApp()
            sys.exit(app.exec_())
