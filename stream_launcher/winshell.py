"""Windows: collegamenti .lnk con AppUserModelID, via COM con ctypes (nessuna dipendenza).

La barra delle applicazioni mostra icona e nome del collegamento del menu Start
che ha lo stesso AppUserModelID della finestra: è così che una finestra di
Firefox appare come «Netflix» con l'icona di Netflix.
"""
from __future__ import annotations

import ctypes
import uuid
from ctypes import POINTER, WINFUNCTYPE, byref, c_int, c_long, c_uint32, c_ushort, c_void_p, c_wchar_p

CLSID_SHELL_LINK = "00021401-0000-0000-C000-000000000046"
IID_SHELL_LINK_W = "000214F9-0000-0000-C000-000000000046"
IID_PERSIST_FILE = "0000010B-0000-0000-C000-000000000046"
IID_PROPERTY_STORE = "886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"
PKEY_AUMID = ("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3", 5)
VT_LPWSTR = 31


class GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]

    @classmethod
    def of(cls, text: str) -> "GUID":
        g = cls()
        ctypes.memmove(g.data, uuid.UUID(text).bytes_le, 16)
        return g


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", c_uint32)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", c_ushort), ("reserved", c_ushort * 3), ("value", c_void_p), ("extra", c_void_p)]


def aumid_key() -> PROPERTYKEY:
    return PROPERTYKEY(GUID.of(PKEY_AUMID[0]), PKEY_AUMID[1])


def call(obj: c_void_p, index: int, argtypes: tuple, *args) -> None:
    """Chiama il metodo `index` della vtable dell'oggetto COM `obj`."""
    vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
    hr = WINFUNCTYPE(c_long, c_void_p, *argtypes)(vtable[index])(obj, *args)
    if hr < 0:
        raise OSError(f"errore COM 0x{hr & 0xFFFFFFFF:08X}")


def release(obj: c_void_p) -> None:
    if obj:
        vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
        WINFUNCTYPE(c_uint32, c_void_p)(vtable[2])(obj)


def query(obj: c_void_p, iid: str) -> c_void_p:
    out = c_void_p()
    call(obj, 0, (POINTER(GUID), POINTER(c_void_p)), byref(GUID.of(iid)), byref(out))
    return out


def create_shortcut(path: str, target: str, args: str, workdir: str, desc: str, icon: str, aumid: str) -> None:
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx(None, 2)  # STA; se il thread è già inizializzato va bene lo stesso
    link, store, persist = c_void_p(), c_void_p(), c_void_p()
    hr = ole32.CoCreateInstance(byref(GUID.of(CLSID_SHELL_LINK)), None, 1, byref(GUID.of(IID_SHELL_LINK_W)), byref(link))
    if hr < 0:
        raise OSError(f"ShellLink non disponibile (0x{hr & 0xFFFFFFFF:08X})")
    try:
        # Vtable di IShellLinkW: 20 SetPath, 11 SetArguments, 9 SetWorkingDirectory,
        # 7 SetDescription, 17 SetIconLocation.
        for index, value in ((20, target), (11, args), (9, workdir), (7, desc)):
            call(link, index, (c_wchar_p,), value)
        if icon:
            call(link, 17, (c_wchar_p, c_int), icon, 0)
        if aumid:
            store = query(link, IID_PROPERTY_STORE)
            buf = ctypes.create_unicode_buffer(aumid)
            value = PROPVARIANT(vt=VT_LPWSTR, value=ctypes.cast(buf, c_void_p))
            call(store, 6, (POINTER(PROPERTYKEY), POINTER(PROPVARIANT)), byref(aumid_key()), byref(value))  # SetValue
            call(store, 7, ())  # Commit
        persist = query(link, IID_PERSIST_FILE)
        call(persist, 6, (c_wchar_p, c_int), path, 1)  # IPersistFile::Save
    finally:
        for obj in (persist, store, link):
            release(obj)


def firefox_aumid(profile_dir: str) -> str:
    """AppUserModelID che Firefox dà alle finestre con taskbar.grouping.useprofile:
    l'hash di Mozilla (mfbt HashString) del percorso del profilo, in decimale."""
    h = 0
    for byte in profile_dir.encode("utf-8"):
        h = (0x9E3779B9 * ((((h << 5) | (h >> 27)) & 0xFFFFFFFF) ^ byte)) & 0xFFFFFFFF
    return str(h)
