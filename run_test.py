from mockupgen import Mockup, setup_logging
setup_logging('INFO')
m = Mockup.open('tests/mockup.psd')
m.replace_smart_object('front', 'tests/texture.png')
m.export('tests/exported.png')
