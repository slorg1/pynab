from django.test import TestCase, Client
from nabsurprised.models import Config
import datetime

class TestView(TestCase):
  def setUp(self):
    Config.load()

  def test_get_settings(self):
    c = Client()
    response = c.get('/nabsurprised/settings')
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.templates[0].name, 'nabsurprised/settings.html')
    self.assertTrue('config' in response.context)
    config = Config.load()
    self.assertEqual(response.context['config'], config)
    self.assertEqual(config.surprise_frequency, 30)
    self.assertEqual(config.next_surprise, None)

  def test_set_frequency(self):
    c = Client()
    response = c.post('/nabsurprised/settings', {'surprise_frequency': 10})
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.templates[0].name, 'nabsurprised/settings.html')
    self.assertTrue('config' in response.context)
    config = Config.load()
    self.assertEqual(response.context['config'], config)
    self.assertEqual(config.surprise_frequency, 10)
    self.assertEqual(config.next_surprise, None)

  def test_surprise_now(self):
    c = Client()
    response = c.put('/nabsurprised/settings')
    self.assertEqual(response.status_code, 200)
    response_json = response.json()
    self.assertTrue('status' in response_json)
    self.assertEqual(response_json['status'], 'ok')
    config = Config.load()
    now = datetime.datetime.now(datetime.timezone.utc)
    self.assertTrue(config.next_surprise < now)
    self.assertTrue(config.next_surprise > now - datetime.timedelta(seconds=15))
