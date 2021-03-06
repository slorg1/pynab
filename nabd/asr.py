import struct
import numpy as np
import traceback
from concurrent.futures import ThreadPoolExecutor
from kaldiasr.nnet3 import KaldiNNet3OnlineModel, KaldiNNet3OnlineDecoder

class ASR:
  """
  Class handling automatic speech recognition.
  """
  MODELS = {
    'fr_FR': '/opt/kaldi/model/kaldi-nabaztag-fr-r20190518'
  }
  DEFAULT_LOCALE = 'fr_FR'
  
  def __init__(self, locale):
    self.executor = ThreadPoolExecutor(max_workers=1)
    self._load_model(locale)

  def _load_model(self, locale):
    if locale in ASR.MODELS:
      path = ASR.MODELS[locale]
    else:
      path = ASR.MODELS[ASR.DEFAULT_LOCALE]
    self.model = KaldiNNet3OnlineModel(path)
    self.decoder = KaldiNNet3OnlineDecoder(self.model)

  def decode_chunk(self, samples, finalize):
    self.executor.submit(lambda s=samples, f=finalize: self._decode_chunk(s, f))

  def _decode_chunk(self, frames, finalize):
    try:
      nframes = len(frames) / 2
      samples = struct.unpack_from('<%dh' % nframes, frames)
      self.decoder.decode(16000, np.array(samples, dtype=np.float32), finalize)
    except Exception:
      print(traceback.format_exc())

  async def get_decoded_string(self, sync):
    if sync:
      future = self.executor.submit(lambda : self._get_decoded_string())
      return future.result()
    else:
      # not sure we could do that
      s, l = self.decoder.get_decoded_string()
      return s

  def _get_decoded_string(self):
    try:
      s, l = self.decoder.get_decoded_string()
      return s
    except Exception:
      print(traceback.format_exc())
