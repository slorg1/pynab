import sys, asyncio, datetime, random
from nabcommon.nabservice import NabService

class Nab8Balld(NabService):
  DAEMON_PIDFILE = '/var/run/nab8balld.pid'

  def __init__(self):
    super().__init__()
    self.interactive = False

  def __config(self):
    from . import models
    return models.Config.load()

  async def reload_config(self):
    from django.core.cache import cache
    cache.clear()
    from . import models
    await self.setup_listener()

  def setup_listener(self):
    config = self.__config()
    if config.enabled:
      packet = '{"type":"mode","mode":"idle","events":["button"],"request_id":"idle-button"}\r\n'
    else:
      packet = '{"type":"mode","mode":"idle","events":[],"request_id":"idle-disabled"}\r\n'
    self.writer.write(packet.encode('utf8'))

  async def process_nabd_packet(self, packet):
    if packet['type'] == 'button_event':
      if not self.interactive:
        if packet['event'] == 'click_and_hold':
          resp = '{"type":"mode","mode":"interactive","events":["button"],"request_id":"set-interactive"}\r\n'
          self.writer.write(resp.encode('utf8'))
      else:
        if packet['event'] == 'up':
          resp = '{"type":"command","sequence":[{"audio":["nab8balld/acquired.mp3"]}],"request_id":"play-acquired"}\r\n'
          self.writer.write(resp.encode('utf8'))
          resp = '{"type":"message","body":[{"audio":["nab8balld/answers/*.mp3"]}],"request_id":"play-answer"}\r\n'
          self.writer.write(resp.encode('utf8'))
          self.interactive = False
          self.setup_listener()
    if packet['type'] == 'response' and 'request_id' in packet and packet['request_id'] == 'set-interactive':
      self.interactive = True
      resp = '{"type":"command","sequence":[{"audio":["nab8balld/listen.mp3"]}],"request_id":"play-listen"}\r\n'
      self.writer.write(resp.encode('utf8'))

  def run(self):
    super().connect()
    self.loop = asyncio.get_event_loop()
    self.setup_listener()
    try:
      self.loop.run_forever()
    except KeyboardInterrupt:
      pass
    finally:
      self.running = False  # signal to exit
      self.writer.close()
      if sys.version_info >= (3,7):
        tasks = asyncio.all_tasks(self.loop)
      else:
        tasks = asyncio.Task.all_tasks(self.loop)
      for t in [t for t in tasks if not (t.done() or t.cancelled())]:
        self.loop.run_until_complete(t)    # give canceled tasks the last chance to run
      self.loop.close()

if __name__ == '__main__':
  Nab8Balld.main(sys.argv[1:])
