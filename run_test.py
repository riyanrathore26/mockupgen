from mockupgen import Mockup, setup_logging

# Show detailed logs (helpful for beginners)
setup_logging("DEBUG")

# 1. Open the PSD
m = Mockup.open("tests/mockup.psd")

# 2. See what smart objects exist
print("Smart objects:", m.list_smart_objects())
# Expected: something like ['front', 'left', 'right', ...]

# 3. Replace the "front" smart object with your design
m.replace_smart_object("front", "tests/small.png")

# 4. Save the result
m.save("tests/output.psd")

print("Done! Open tests/output.psd in Photoshop.")

