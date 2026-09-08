import os, sys
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='windows'
os.environ.pop('QT_QUICK_BACKEND',None)
repo=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(repo))
sys.path.insert(0,str(repo/'aquameasure-pyside/tests'))
from test_measure_qml_runtime import MeasureQmlRuntimeTest
from PySide6.QtCore import QObject, QPoint
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
import cv2, numpy as np
case=MeasureQmlRuntimeTest('test_playback_zoom_pan_and_peck_badge_share_image_coordinates')
case.setUpClass()
try:
    tmp=Path(case._tmp.name)
    raw=np.zeros((300,400,3),np.uint8)
    raw[:]=(35,25,18)
    cv2.ellipse(raw,(190,140),(12,7),0,0,360,(0,180,250),-1)
    yy,xx=np.indices((300,400),dtype=np.float32)
    rect=cv2.remap(raw,xx+10,yy+10,cv2.INTER_LINEAR)
    movie=tmp/'synthetic.avi'
    writer=cv2.VideoWriter(str(movie),cv2.VideoWriter_fourcc(*'MJPG'),25,(400,300))
    for _ in range(150): writer.write(raw)
    writer.release()
    rgb=cv2.cvtColor(rect,cv2.COLOR_BGR2RGB)
    case.images.set_image('measure_left',QImage(rgb.data,400,300,rgb.strides[0],QImage.Format_RGB888).copy())
    qml='''import QtQuick
import QtQuick.Window
import AquaMeasure
Window { width: 400; height: 300
MeasureStereoView { objectName: "proofView"; anchors.fill: parent
sideLabel: "G"; isLeft: true; videoPath: "VIDEO"; videoFrameCount: 150
frameWidth: 400; frameHeight: 300; fps: 25; absFrame: 0
placementEnabled: false; interactionEnabled: false; rectifiedReady: true; playing: false
} }'''.replace('VIDEO',movie.as_posix())
    window=case._create_window(qml)
    view=window.findChild(QObject,'proofView')
    QTest.mouseMove(window,QPoint(399,299))
    fish=case.controller.fish()
    fish._overlay_trails=[]
    fish._focus_box={'valid':False}
    view.setProperty('viewZoom',4.)
    view.setProperty('panX',20.)
    view.setProperty('panY',30.)
    output=repo/'aquameasure-pyside/tools/output/reprise-agents'
    output.mkdir(parents=True,exist_ok=True)
    for playing,cx,cy,name in [(False,180,130,'pause-rectifiee-x4'),(True,190,140,'lecture-brute-x4')]:
        view.setProperty('playing',playing)
        fish._overlay_boxes=[{'x1':cx-15,'y1':cy-10,'x2':cx+15,'y2':cy+10,'speciesName':'Poisson test','identificationSource':'validated','trackId':7}]
        fish.overlayChanged.emit()
        QTest.qWait(400)
        shot=window.grabWindow()
        assert not shot.isNull(), 'No rendered image'
        shot.save(str(output/(name+'.png')))
        px=int(view.property('_contentOx')+cx*view.property('_viewScale'))
        py=int(view.property('_contentOy')+cy*view.property('_viewScale'))
        color=shot.pixelColor(px,py)
        print(name,'fish at',px,py,'RGB',color.getRgb())
        assert color.red()>170 and color.green()>100 and color.blue()<100, 'Overlay misses rendered fish'
    case.controller.pecks().peckPopped.emit(190,140,'B','#f59e0b','Bouchee')
    QTest.qWait(160)
    window.grabWindow().save(str(output/'bouchee-lecture-x4.png'))
    view.setProperty('playing',False)
    print('VISUAL REGISTRATION OK')
finally:
    if 'view' in locals():
        view.setProperty('playing',False)
        view.setProperty('videoPath','')
        QTest.qWait(100)
    case.tearDownClass()
