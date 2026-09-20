"""Run in a fresh bundled process with QT_SCALE_FACTOR=1.5; no project writes."""
import argparse
import json
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from stimulus_harvester.gui.window import Window

parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
app=QApplication([]);window=Window();window.show();window.resize(850,690)
result={}


def capture():
    if window.session.busy:
        QTimer.singleShot(100,capture);return
    picture=window.grab();picture.save(str(args.output/'display-150-percent.png'))
    result.update(logical_width=window.width(),logical_height=window.height(),pixel_width=picture.width(),pixel_height=picture.height(),device_pixel_ratio=picture.devicePixelRatioF(),platform=app.platformName())
    (args.output/'display.json').write_text(json.dumps(result,indent=2))
    window.close();app.quit()


QTimer.singleShot(1500,capture)
QTimer.singleShot(30000,app.quit)
app.exec()
assert result['device_pixel_ratio']>=1.5,result
assert result['pixel_width']>=result['logical_width']*1.5,result
print(json.dumps(result))
