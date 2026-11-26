#!/usr/bin/env python3
"""Bounded standalone adapter acceptance using only private loopback test PVs.

The IOC is a synthetic current/readback/camera surface, not a machine model.
It validates Channel Access, fresh frame statistics and verified restoration.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from uuid import uuid4

from check_emit_infobax_ioc import private_environment, unused_port, stop


def worker(output, prefix, sampling="timed"):
    port=os.environ.get("EPICS_CA_SERVER_PORT","")
    if (os.environ.get("EPICS_CA_ADDR_LIST")!=f"127.0.0.1:{port}"
            or os.environ.get("EPICS_CA_AUTO_ADDR_LIST")!="NO"
            or os.environ.get("EPICS_CAS_INTF_ADDR_LIST")!="127.0.0.1"
            or not prefix.startswith("INFOBAX_TEST_")):
        raise RuntimeError("Private loopback CA environment and test prefix required")
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    from repo_bootstrap import ensure_repo_import_path
    ensure_repo_import_path(__file__)
    import threading
    import numpy as np
    import epics
    from half_linac.src.shared.machine_profile import load_app_context
    from half_linac.src.apps.emit_measure.infobax_camera import CameraSettings,CameraPointAcquirer
    from half_linac.src.apps.emit_measure.infobax_machine import HalfRealInfoBAXMachine
    from half_linac.src.apps.emit_measure.infobax_workflow import WorkflowConfig,InfoBAXWorkflow
    from half_linac.src.apps.emit_measure.optimization import OptimizationVariable
    from half_linac.src.optimization.infobax import InfoBAXConfig
    context=load_app_context("emit_measure",machine_id="half",control_backend="real")
    names={"sol_set":prefix+"SS01:current_set","sol_read":prefix+"SS01:current_readback",
           "k1":prefix+"QL09:K1","qset":prefix+"QL09:current_set","qread":prefix+"QL09:current_readback",
           "image":prefix+"PRF03:image","frame":prefix+"PRF03:frame_id","exposure":prefix+"PRF03:exposure_time"}
    if sampling=="timed":
        del names["frame"]
    elements=[]
    for element in context.profile.elements:
        channels={k:{**v,"real":f"{prefix}{element.id}:{k}"} for k,v in element.channels.items()}
        if element.id=="PRF03" and sampling=="verified": channels["frame_id"]={"real":names["frame"]}
        elements.append(replace(element,channels=channels))
    profile=replace(context.profile,elements=tuple(elements),_elements_by_id={e.id:e for e in elements})
    context=replace(context,profile=profile)
    output.mkdir(parents=True,exist_ok=True)
    records=[]
    for name,pv in names.items():
        if name=="image":
            records.append(f'record(waveform, "{pv}") {{ field(FTVL,"DOUBLE") field(NELM,"4096") }}')
        else:
            value=1. if name.startswith("sol") else .1 if name=="exposure" else 0.
            records.append(f'record(ao, "{pv}") {{ field(VAL,"{value}") field(PREC,"8") }}')
    database=output/"test.db"; database.write_text("\n".join(records))
    with ExitStack() as stack:
        logfile=stack.enter_context((output/"ioc.log").open("w"))
        ioc=subprocess.Popen([shutil.which("softIoc") or "softIoc","-S","-d",str(database)],
                             cwd=output,stdout=logfile,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL)
        stack.callback(stop,ioc)
        deadline=time.monotonic()+15
        while epics.caget(names["sol_set"],timeout=.2,use_monitor=False) is None:
            if ioc.poll() is not None or time.monotonic()>deadline: raise RuntimeError("IOC startup failed")
            time.sleep(.05)
        mutex=threading.RLock(); cancelled=threading.Event(); errors=[]; writes=[]
        def read(pv,**kwargs):
            assert pv.startswith(prefix), "Non-test PV forbidden"
            with mutex: return epics.caget(pv,**kwargs)
        def write(pv,value,**kwargs):
            assert pv.startswith(prefix), "Non-test PV forbidden"
            with mutex:
                writes.append((pv,value))
                result=epics.caput(pv,value,**kwargs)
                if result!=1: return result
                if pv==names["sol_set"]: epics.caput(names["sol_read"],value,wait=True,timeout=2)
                if pv==names["k1"]:
                    for key in ("qset","qread"): epics.caput(names[key],value,wait=True,timeout=2)
                return result
        def publish():
            epics.ca.use_initial_context()
            frame=0
            a=np.arange(64)+.5-32; x,y=np.meshgrid(a,a)
            try:
                while not cancelled.is_set():
                    with mutex:
                        current=float(epics.caget(names["sol_set"],timeout=1,use_monitor=False))
                        k1=float(epics.caget(names["k1"],timeout=1,use_monitor=False))
                        scale=1.+.5*((current-.5)/2.)**2
                        variance=(.08-.03*k1+.06*k1*k1)*scale*(1.+.003*np.sin(frame))
                        image=1000*np.exp(-x*x/(2*variance/.01)-y*y/(2*25.))
                        frame+=1
                        epics.caput(names["image"],image.ravel(),wait=True,timeout=2)
                        if sampling=="verified":epics.caput(names["frame"],frame,wait=True,timeout=2)
                    cancelled.wait(.035)
            except Exception as exc: errors.append(str(exc))
        thread=threading.Thread(target=publish,daemon=True); thread.start()
        def stop_camera():
            cancelled.set(); thread.join(timeout=5)
        stack.callback(stop_camera)
        scans=tuple(np.linspace(-1,1,5))
        cfg=WorkflowConfig(0,2,scans,tuple((1.,k) for k in scans),225,active_queries=0,max_minutes=1)
        if sampling=="verified":
            camera=CameraPointAcquirer(CameraSettings((64,64),.1,timeout=5),get=read,
                image_pv=names["image"],frame_id_pv=names["frame"],exposure_pv=names["exposure"],identity_verified=True)
            machine=HalfRealInfoBAXMachine(context,OptimizationVariable("SS01",0,2),cfg,camera,
                scan_quad="QL09",get=read,put=write,settle_time=0,motion_timeout=2)
        else:
            from types import SimpleNamespace
            from half_linac.src.apps.emit_measure.infobax_framework import FrameworkInfoBAXMachine
            paras=SimpleNamespace(app_context=context,quad_name="QL09",flag_name="PRF03",
                flagImagePV=names["image"],flagExposurePV=names["exposure"],flag_pixel_shape=(64,64),
                flag_image_extent=(-3.2,3.2,-3.2,3.2),samples=5,settle_time=.08,sample_interval=.05,
                beam_width_method="Gaussian fit",roi=None,background_image=None,fit_vmin=None,
                flag_image_flip_y=False,model_snapshot_metadata={"fields":[]})
            machine=FrameworkInfoBAXMachine(paras,OptimizationVariable("SS01",0,2),cfg,
                get=read,put=write,solenoid_settle=0,motion_timeout=2)
            camera=machine.camera
        approvals=[]
        workflow=InfoBAXWorkflow(cfg,machine,output/"run",approve=lambda r,c:approvals.append(r) or True,
                                 numerical_config=InfoBAXConfig(summary_samples=64))
        from contextlib import redirect_stdout
        with (output/"workflow.log").open("w") as detail, redirect_stdout(detail):
            result=workflow.run()
        if result["status"]!="completed" or result["restoration_status"]!="succeeded":
            raise RuntimeError(json.dumps({k:result[k] for k in ("status","error","restoration_status")}))
        machine.verify_snapshot(workflow.initial)
        assert len(approvals)==8 and len(workflow.records)==16
        assert len(list((output/"run"/"frames").glob("*.npz")))==80
        if sampling=="verified":
            assert all(len(set(f["frame_id"] for f in r["measurement"]["metadata"]["frames"]))==5
                       for r in workflow.records)
        else:
            assert "frame" not in names
            assert all(r["measurement"]["metadata"]["freshness"]=="timed_unverified" for r in workflow.records)
        if errors: raise RuntimeError("; ".join(errors))
        report={"status":"passed","pv_prefix":prefix,"ca_address":os.environ["EPICS_CA_ADDR_LIST"],
                "points":len(workflow.records),"approvals":len(approvals),"raw_frames":80,
                "camera_statistics":camera.statistics,"restoration_status":workflow.restoration_status,
                "sampling":sampling,"scope":"synthetic softIOC; no elegant or site PVs"}
        (output/"result.json").write_text(json.dumps(report,indent=2))
        print(json.dumps(report))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--sampling",choices=("timed","verified"),default="timed")
    parser.add_argument("--worker",action="store_true",help=argparse.SUPPRESS)
    parser.add_argument("--prefix",help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.worker:
        worker(args.output.resolve(),args.prefix,args.sampling)
        return 0
    port,repeater=unused_port(),unused_port()
    while repeater==port: repeater=unused_port()
    environment=private_environment(port,repeater)
    environment["MPLCONFIGDIR"]="/tmp/emit-infobax-ioc-mpl"
    prefix="INFOBAX_TEST_"+uuid4().hex[:10]+":"
    command=[sys.executable,str(Path(__file__).resolve()),"--worker","--output",str(args.output.resolve()),"--prefix",prefix,"--sampling",args.sampling]
    return subprocess.run(command,env=environment,timeout=90).returncode


if __name__=="__main__": raise SystemExit(main())
