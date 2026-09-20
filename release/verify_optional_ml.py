"""Explicit optional CPU inference smoke. Uses a caller-specified cache, never a default policy."""
import argparse
import json
from pathlib import Path
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    import torch,torchvision
    from PIL import Image
    from stimulus_harvester.screening.config import DetectorConfig
    from stimulus_harvester.screening.detectors import build_detector,normalize
    from stimulus_harvester.workspace import dependency_states,default_configuration
    assert torch.version.cuda is None and not torch.cuda.is_available()
    assert not default_configuration('no defaults').screening.detectors
    image=args.output/'synthetic plain image.png';Image.new('RGB',(320,240),'gray').save(image)
    config=DetectorConfig(id='explicit-release-smoke',property='human_presence',model='fasterrcnn_mobilenet_v3_large_320_fpn',device='cpu',low_threshold=.15,high_threshold=.6)
    start=time.perf_counter();detector=build_detector(config);raw=detector.predict(image)
    result={'purpose':'Provisioning and inference only, not accuracy evaluation or production-default selection','torch':torch.__version__,'torchvision':torchvision.__version__,'CUDA':torch.version.cuda,'capabilities':dependency_states(probe=True),'identity':detector.identity,'runtime':detector.runtime,'raw':raw,'normalized':normalize(raw,config),'seconds':time.perf_counter()-start}
    detector.close()
    from PySide6.QtWidgets import QApplication
    from stimulus_harvester.gui.window import Window
    app=QApplication([]);window=Window();assert window.tabs.count()==6;window.close()
    result['optional_desktop_constructed']=True
    (args.output/'optional-ml.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
