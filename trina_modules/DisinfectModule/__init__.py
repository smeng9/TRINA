import os, sys
if(sys.version_info[0] < 3):
    # from future import *
    from imp import reload

else:
    from importlib import reload

path = os.path.expanduser('~/TRINA/Motion/')
sys.path.append(path)




from . import DisinfectFile
reload(DisinfectFile)

from DisinfectFile import Disinfect

sys.path.remove(path)
