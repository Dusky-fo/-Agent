"""Fixed window size: one size for the whole session, clamped to smaller screens."""
def initial_size(screen_width,screen_height):
    width = min(1150, max(640, screen_width - 50))
    height = min(800, max(400, screen_height - 90))
    return int(width), int(height)


def centered_position(parent, size, work):
    px,py,pw,ph = parent; width,height = size
    left,top,right,bottom = work
    x = px+(pw-width)//2; y = py+(ph-height)//2
    return max(left,min(x,right-width)), max(top,min(y,bottom-height))


def work_area(root):
    work = (root.winfo_vrootx(),root.winfo_vrooty(),root.winfo_vrootx()+root.winfo_vrootwidth(),root.winfo_vrooty()+root.winfo_vrootheight())
    try:
        import ctypes
        from ctypes import wintypes as w
        class Info(ctypes.Structure):
            _fields_ = [("cbSize",w.DWORD),("monitor",w.RECT),("work",w.RECT),("flags",w.DWORD)]
        api = ctypes.windll.user32
        api.MonitorFromWindow.argtypes = [w.HWND,w.DWORD]; api.MonitorFromWindow.restype = w.HANDLE
        api.GetMonitorInfoW.argtypes = [w.HANDLE,ctypes.POINTER(Info)]; api.GetMonitorInfoW.restype = w.BOOL
        info = Info(); info.cbSize = ctypes.sizeof(info)
        if api.GetMonitorInfoW(api.MonitorFromWindow(root.winfo_id(),2),ctypes.byref(info)):
            r = info.work; work = (r.left,r.top,r.right,r.bottom)
    except (AttributeError,OSError): pass
    return work


def center_dialog(root, dialog, width, height):
    root.update_idletasks();work=work_area(root)
    border = max(0,root.winfo_rootx()-root.winfo_x())
    title = max(0,root.winfo_rooty()-root.winfo_y())
    width = min(width,max(320,work[2]-work[0]-24-2*border)); height = min(height,max(300,work[3]-work[1]-24-title-border))
    parent = (root.winfo_x(),root.winfo_y(),root.winfo_width()+2*border,root.winfo_height()+title+border)
    x,y = centered_position(parent,(width+2*border,height+title+border),work)
    dialog.minsize(min(560,width),min(460,height))
    dialog.geometry("%sx%s+%s+%s" % (width,height,x,y))
