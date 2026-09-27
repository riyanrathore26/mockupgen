from mockupgen import Mockup, setup_logging
setup_logging("INFO")

m = Mockup.open("tests/mockup3.psd")   # or your PSD
print("SOs:", m.list_smart_objects())
m.replace_smart_object("front", "tests/texture.png")   # actual size
m.export("exported.png", fabric_strength=0.25)