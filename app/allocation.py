import os

def physical_size(path,info):
    if os.name!='nt':return getattr(info,'st_blocks',0)*512
    import ctypes
    from ctypes import wintypes
    function=ctypes.WinDLL('kernel32',use_last_error=True).GetCompressedFileSizeW
    function.argtypes=[wintypes.LPCWSTR,ctypes.POINTER(wintypes.DWORD)];function.restype=wintypes.DWORD
    high=wintypes.DWORD();ctypes.set_last_error(0);low=function(str(path),ctypes.byref(high))
    if low==0xffffffff and ctypes.get_last_error():return None
    return (high.value<<32)|low
