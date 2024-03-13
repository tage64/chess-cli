# Add the dlls directory to path:
import os
basepath = os.path.dirname(os.path.abspath(__file__))
dllspath = os.path.join(basepath, "..", "dlls")
os.environ['PATH'] = dllspath + os.pathsep + os.environ['PATH']
