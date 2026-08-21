from enum import IntFlag

import comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0 as __wrapper_module__
from comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0 import (
    VARIANT_BOOL, OLE_XPOS_CONTAINER, Font, IFontEventsDisp, _lcid,
    StdFont, StdPicture, IPicture, typelib_path, Unchecked, Checked,
    OLE_XSIZE_HIMETRIC, IFont, EXCEPINFO, FONTSTRIKETHROUGH,
    OLE_YSIZE_CONTAINER, Default, IEnumVARIANT, OLE_HANDLE,
    OLE_ENABLEDEFAULTBOOL, OLE_YPOS_PIXELS, IUnknown, CoClass, GUID,
    FONTBOLD, IDispatch, OLE_XPOS_PIXELS, HRESULT, OLE_COLOR, dispid,
    OLE_YPOS_CONTAINER, OLE_OPTEXCLUSIVE, IPictureDisp, FONTNAME,
    COMMETHOD, IFontDisp, OLE_YSIZE_PIXELS, DISPMETHOD, FONTITALIC,
    _check_version, DISPPROPERTY, OLE_CANCELBOOL, OLE_YSIZE_HIMETRIC,
    Monochrome, VgaColor, Gray, OLE_YPOS_HIMETRIC, DISPPARAMS,
    FontEvents, Picture, BSTR, OLE_XSIZE_CONTAINER, OLE_XSIZE_PIXELS,
    Library, FONTSIZE, FONTUNDERSCORE, Color, OLE_XPOS_HIMETRIC
)


class OLE_TRISTATE(IntFlag):
    Unchecked = 0
    Checked = 1
    Gray = 2


class LoadPictureConstants(IntFlag):
    Default = 0
    Monochrome = 1
    VgaColor = 2
    Color = 4


__all__ = [
    'FONTBOLD', 'OLE_TRISTATE', 'OLE_XPOS_CONTAINER',
    'OLE_XPOS_PIXELS', 'OLE_COLOR', 'OLE_YPOS_CONTAINER', 'Font',
    'OLE_OPTEXCLUSIVE', 'IFontEventsDisp', 'IPictureDisp', 'FONTNAME',
    'IFontDisp', 'StdFont', 'StdPicture', 'OLE_YSIZE_PIXELS',
    'typelib_path', 'FONTITALIC', 'IPicture', 'Unchecked',
    'OLE_CANCELBOOL', 'Checked', 'OLE_YSIZE_HIMETRIC', 'Monochrome',
    'VgaColor', 'LoadPictureConstants', 'Gray', 'OLE_XSIZE_HIMETRIC',
    'OLE_YPOS_HIMETRIC', 'FontEvents', 'IFont', 'Picture',
    'OLE_XSIZE_CONTAINER', 'FONTSTRIKETHROUGH', 'OLE_YSIZE_CONTAINER',
    'OLE_XSIZE_PIXELS', 'Library', 'Default', 'OLE_HANDLE',
    'OLE_ENABLEDEFAULTBOOL', 'FONTSIZE', 'OLE_YPOS_PIXELS',
    'FONTUNDERSCORE', 'Color', 'OLE_XPOS_HIMETRIC'
]

