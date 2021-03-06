import wave
from mpg123 import Mpg123
import alsaaudio
import asyncio
from concurrent.futures import ThreadPoolExecutor
from .sound import Sound
from .nabio import NabIO
import traceback

class SoundAlsa(Sound):
  MODEL_2018_CARD_NAME = 'sndrpihifiberry'
  MODEL_2019_CARD_NAME = 'seeed2micvoicec'

  def __init__(self, hw_model):
    if hw_model == NabIO.MODEL_2018:
      self.playback_device = 'plughw:CARD=' + SoundAlsa.MODEL_2018_CARD_NAME
      self.playback_mixer = None
      self.record_device = 'null'
      self.record_mixer = None
    if hw_model == NabIO.MODEL_2019_TAG or hw_model == NabIO.MODEL_2019_TAGTAG:
      card_index = alsaaudio.cards().index(SoundAlsa.MODEL_2019_CARD_NAME)
      self.playback_device = 'plughw:CARD=' + SoundAlsa.MODEL_2019_CARD_NAME
      self.playback_mixer = alsaaudio.Mixer(control='Playback', cardindex=card_index)
      self.record_device = self.playback_device
      self.record_mixer = alsaaudio.Mixer(control='Capture', cardindex=card_index)
    if not SoundAlsa.test_device(self.playback_device, False):
      raise RuntimeError('Unable to configure sound card for playback')
    if self.record_device != 'null' and not SoundAlsa.test_device(self.record_device, True):
      raise RuntimeError('Unable to configure sound card for recording')
    self.executor = ThreadPoolExecutor(max_workers=1)
    self.future = None
    self.currently_playing = False
    self.currently_recording = False

  @staticmethod
  def sound_card():
    sound_cards = alsaaudio.cards()
    for sound_card in alsaaudio.cards():
      if sound_card in [SoundAlsa.MODEL_2018_CARD_NAME, SoundAlsa.MODEL_2019_CARD_NAME]:
        return sound_card
    raise RuntimeError('Sound card not found by ALSA (are drivers missing?)')

  @staticmethod
  def test_device(device, record):
    """
    Test selected ALSA device, making sure it handles both stereo and mono and
    both 44.1KHz and 22.05KHz on output, mono and 16 kHz on input.
    On a typical RPI configuration, default with hifiberry card is not
    configured to do software-mono, so we'll use plughw:CARD=sndrpihifiberry instead.
    Likewise, on 2019 cards, hw:CARD=seeed2micvoicec is not able to run mono
    sound.
    """
    try:
      dev = None
      if record:
        dev = alsaaudio.PCM(alsaaudio.PCM_CAPTURE, device=device)
      else:
        dev = alsaaudio.PCM(device=device)
      if dev.setformat(alsaaudio.PCM_FORMAT_S16_LE) != alsaaudio.PCM_FORMAT_S16_LE:
        return False
      if record:
        if dev.setchannels(1) != 1:
          return False
        if dev.setrate(16000) != 16000:
          return False
      else:
        if dev.setchannels(2) != 2:
          return False
        if dev.setchannels(1) != 1:
          return False
        if dev.setrate(44100) != 44100:
          return False
        if dev.setrate(22050) != 22050:
          return False
    except alsaaudio.ALSAAudioError:
      return False
    finally:
      if dev:
        dev.close()
    return True

  async def start_playing_preloaded(self, filename):
    await self.stop_playing()
    self.currently_playing = True
    self.future = asyncio.get_event_loop().run_in_executor(self.executor, lambda f=filename: self._play(f))

  def _play(self, filename):
    try:
      device = alsaaudio.PCM(device=self.playback_device)
      if filename.endswith('.wav'):
        with wave.open(filename, 'rb') as f:
          channels = f.getnchannels()
          width = f.getsampwidth()
          rate = f.getframerate()
          self._setup_device(device, channels, rate, width)
          periodsize = int(rate / 10) # 1/10th of second
          device.setperiodsize(periodsize)
          data = f.readframes(periodsize)
          chunksize = periodsize * channels * width
          while data and self.currently_playing:
            if len(data) < chunksize:
              # This (probably) is last iteration.
              # ALSA device expects chunks of fixed period size
              # Pad the sound with silence to complete chunk
              data = data + bytearray(chunksize - len(data))
            device.write(data)
            data = f.readframes(periodsize)
      elif filename.endswith('.mp3'):
        mp3 = Mpg123(filename)
        rate, channels, encoding = mp3.get_format()
        width = mp3.get_width_by_encoding(encoding)
        self._setup_device(device, channels, rate, width)
        periodsize = int(rate / 10) # 1/10th of second
        device.setperiodsize(periodsize)
        target_chunk_size = periodsize * width * channels
        chunk = bytearray(0)
        for frame in mp3.iter_frames():
          if len(chunk) + len(frame) < target_chunk_size:
            # Chunk is still smaller than what ALSA device expects (0.1 sec)
            chunk = chunk + frame
          else:
            remaining = target_chunk_size - len(chunk)
            chunk = chunk + frame[:remaining]
            device.write(chunk)
            chunk = frame[remaining:]
          if not self.currently_playing:
            break
        # ALSA device expects chunks of fixed period size
        # Pad the sound with silence to complete last chunk
        if len(chunk) > 0:
          remaining = target_chunk_size - len(chunk)
          chunk = chunk + bytearray(remaining)
          device.write(chunk)
    finally:
      self.currently_playing = False
      device.close()

  def _setup_device(self, device, channels, rate, width):
    # Set attributes
    device.setchannels(channels)
    device.setrate(rate)

    # 8bit is unsigned in wav files
    if width == 1:
        device.setformat(alsaaudio.PCM_FORMAT_U8)
    # Otherwise we assume signed data, little endian
    elif width == 2:
        device.setformat(alsaaudio.PCM_FORMAT_S16_LE)
    elif width == 3:
        device.setformat(alsaaudio.PCM_FORMAT_S24_3LE)
    elif width == 4:
        device.setformat(alsaaudio.PCM_FORMAT_S32_LE)
    else:
        raise ValueError('Unsupported format')

  async def stop_playing(self):
    if self.currently_playing:
      self.currently_playing = False
    await self.wait_until_done()

  async def wait_until_done(self):
    if self.future:
      await self.future
    self.future = None

  async def start_recording(self, stream_cb):
    await self.stop_playing()
    self.currently_recording = True
    self.future = asyncio.get_event_loop().run_in_executor(self.executor, lambda cb=stream_cb: self._record(cb))

  def _record(self, cb):
    inp = None
    try:
      inp = alsaaudio.PCM(alsaaudio.PCM_CAPTURE, alsaaudio.PCM_NORMAL, device='default')
      ch = inp.setchannels(1)
      rate = inp.setrate(16000)
      format = inp.setformat(alsaaudio.PCM_FORMAT_S16_LE)
      inp.setperiodsize(1600)   # 100ms
      finalize = False
      while not finalize:
        l, data = inp.read()
        if not self.currently_recording:
          finalize = True
        if l or finalize:
          cb(data, finalize)
    except Exception:
      print(traceback.format_exc())
    finally:
      self.currently_recording = False
      if inp:
        inp.close()

  async def stop_recording(self):
    if self.currently_recording:
      self.currently_recording = False
    await self.wait_until_done()
