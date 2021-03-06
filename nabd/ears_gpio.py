import RPi.GPIO as GPIO
from threading import Condition
import asyncio
import time
import sys
import atexit
from concurrent.futures import ThreadPoolExecutor
from .ears import Ears

@atexit.register
def cleanup_gpio():
  GPIO.setwarnings(False)
  GPIO.cleanup()

class EarsGPIO(Ears):
  ENCODERS_CHANNELS = [24, 23]
  MOTOR_CHANNELS = [[12, 11], [10, 9]]
  ENABLE_CHANNELS = [5, 6]
  HOLES = Ears.STEPS

  FORWARD_INCREMENT = 1
  BACKWARD_INCREMENT = -1

  def __init__(self):
    self.running = [False, False]
    self.targets = [0, 0]
    self.encoder_cv = Condition()
    self.positions = [0, 0]
    self.directions = [1, 1]
    self.executor = ThreadPoolExecutor(max_workers=1)
    self.lock = asyncio.Lock()
    GPIO.setwarnings(True)
    GPIO.setmode(GPIO.BCM)
    for channel in EarsGPIO.ENCODERS_CHANNELS:
      GPIO.setup(channel, GPIO.IN)
      try:
        GPIO.add_event_detect(channel, GPIO.RISING, callback=self._encoder_cb)
      except RuntimeError:
        print('Could not set edge detection (please reboot ?)')
        sys.exit(1)
    for pairs in EarsGPIO.MOTOR_CHANNELS:
      for channel in pairs:
        GPIO.setup(channel, GPIO.OUT)
        GPIO.output(channel, GPIO.LOW)
    for channel in EarsGPIO.ENABLE_CHANNELS:
      GPIO.setup(channel, GPIO.OUT)
      GPIO.output(channel, GPIO.HIGH)

  def _encoder_cb(self, channel):
    """
    Callback from GPIO.
    Thread: Rpi.GPIO event thread
    """
    if channel == EarsGPIO.ENCODERS_CHANNELS[0]:
      ear = 0
    elif channel == EarsGPIO.ENCODERS_CHANNELS[1]:
      ear = 1
    with self.encoder_cv:
      direction = self.directions[ear]
      if direction == 0:
        self.positions[ear] = None
        (loop, callback) = self.callback
        loop.call_soon_threadsafe(lambda ear=ear: callback(ear))
      else:
        self.positions[ear] = (self.positions[ear] + direction) % EarsGPIO.HOLES
        if self.targets[ear] == None: # reset mode
          self.encoder_cv.notify()
        else:
          if self.positions[ear] == self.targets[ear]:
            self._stop_motor(ear)
            self.encoder_cv.notify()
          elif self.positions[ear] == (self.targets[ear] % EarsGPIO.HOLES):
            if self.targets[ear] >= EarsGPIO.HOLES:
              self.targets[ear] = self.targets[ear] - EarsGPIO.HOLES
            elif self.targets[ear] < 0:
              self.targets[ear] = self.targets[ear] + EarsGPIO.HOLES

  def _stop_motor(self, ear):
    """
    Stop motor by changing the channels GPIOs.
    Thread: RPi.GPIO event
    """
    for channel in EarsGPIO.MOTOR_CHANNELS[ear]:
      GPIO.output(channel, GPIO.LOW)
    self.running[ear] = False
    self.directions[ear] = 0

  def _start_motor(self, ear, direction):
    """
    Start motor for given ear.
    ear = 0 or 1
    direction = 1 or -1
    Threads: main loop or executor
    """
    dir_ix = int((1 - direction) / 2)
    GPIO.output(EarsGPIO.MOTOR_CHANNELS[ear][1 - dir_ix], GPIO.LOW)
    GPIO.output(EarsGPIO.MOTOR_CHANNELS[ear][dir_ix], GPIO.HIGH)
    self.running[ear] = True
    self.directions[ear] = direction

  def on_move(self, loop, callback):
    self.callback = (loop, callback)

  async def reset_ears(self, target_left, target_right):
    async with self.lock:
      await asyncio.get_event_loop().run_in_executor(self.executor, self._do_reset_ears, target_left, target_right)

  def _do_reset_ears(self, target_left, target_right):
    """
    Reset ears by running a detection and ignoring the result.
    Thread: executor
    """
    self.positions = [None, None]
    self._run_detection(target_left, target_right)

  def _run_detection(self, target_left, target_right):
    """
    Run detection of any ear in unknown position.
    Thread: executor
    """
    for ear in [0, 1]:
      if self.positions[ear] == None:
        self.positions[ear] = 0
        self.targets[ear] = None
        self._start_motor(ear, EarsGPIO.FORWARD_INCREMENT)
    start = time.time()
    previous_risings = [start, start]
    with self.encoder_cv:
      current_positions = self.positions.copy()
      while self.running[0] or self.running[1]:
        if self.encoder_cv.wait(0.3):
          # Got a signal
          now = time.time()
          for ear in range(2):
            if self.targets[ear] == None and self.positions[ear] != current_positions[ear]:
              delta = now - previous_risings[ear]
              if delta > 0.4:
                # passed the missing hole
                if target_left != None and ear == Ears.LEFT_EAR:
                  self.targets[ear] = target_left
                elif target_right != None and ear == Ears.RIGHT_EAR:
                  self.targets[ear] = target_right
                else:
                  self.targets[ear] = (self.directions[ear] - self.positions[ear]) % EarsGPIO.HOLES
                self.positions[ear] = self.directions[ear]
              current_positions[ear] = self.positions[ear]
              previous_risings[ear] = now
        else:
          # Got no signal.
          now = time.time()
          for ear in range(2):
            if self.targets[ear] == None:
              delta = now - previous_risings[ear]
              if delta > 0.4:
                # At missing hole
                if target_left != None and ear == Ears.LEFT_EAR:
                  self.targets[ear] = target_left
                elif target_right != None and ear == Ears.RIGHT_EAR:
                  self.targets[ear] = target_right
                else:
                  self.targets[ear] = (- self.positions[ear]) % EarsGPIO.HOLES
                self.positions[ear] = 0
    return self.positions.copy()

  async def move(self, motor, delta, direction):
    await self.go(motor, self.targets[motor] + delta, direction)

  async def wait_while_running(self):
    await asyncio.get_event_loop().run_in_executor(self.executor, self._do_wait_while_running)

  def _do_wait_while_running(self):
    """
    Wait until motors are no longer running, using a condition variable.
    Thread: executor
    """
    with self.encoder_cv:
      while self.running[0] or self.running[1]:
        self.encoder_cv.wait()

  async def detect_positions(self):
    """
    Get the position of the ears, running a detection if required.
    """
    async with self.lock:
      if self.positions[0] == None or self.positions[1] == None:
        return await asyncio.get_event_loop().run_in_executor(self.executor, self._run_detection, None, None)
      return (self.positions[0], self.positions[1])

  async def go(self, ear, position, direction):
    """
    Go to a specific position.
    If direction is 0, turn forward, otherwise, turn backward
    If position is not within 0-16, it represents additional turns.
    For example, 17 means to position the ear at 0 after at least a complete turn.
    """
    async with self.lock:
      # Return ears to a known state
      if self.positions[0] == None or self.positions[1] == None:
        await asyncio.get_event_loop().run_in_executor(self.executor, self._run_detection, 0, 0)
      self.targets[ear] = position
      if direction:
        dir = EarsGPIO.BACKWARD_INCREMENT
      else:
        dir = EarsGPIO.FORWARD_INCREMENT
      if self.positions[ear] == self.targets[ear] % EarsGPIO.HOLES:
        if self.targets[ear] >= EarsGPIO.HOLES:
          self.targets[ear] = self.targets[ear] - EarsGPIO.HOLES
        elif self.targets[ear] < 0:
          self.targets[ear] = self.targets[ear] + EarsGPIO.HOLES
        else:
          return  # we already are at requested position
      self._start_motor(ear, dir)
