import sys, os
print("sys.frozen:", getattr(sys, 'frozen', False))
if hasattr(sys, '_MEIPASS'):
    print("sys._MEIPASS:", sys._MEIPASS)
print("__file__:", __file__)
print("dirname(__file__):", os.path.dirname(__file__))
print("abspath(dirname(__file__)):", os.path.abspath(os.path.dirname(__file__)))
