import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_modules"))

from main import start

if __name__ == "__main__":
    start()
