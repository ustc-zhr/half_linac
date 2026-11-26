"""Parity with legacy measurement and InfoBAX's timed point statistics."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('MPLCONFIGDIR','/tmp/emit-share-test-mpl')
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
from half_linac.src.apps.emit_measure.measurement_service import (
    MeasurementSnapshot,PointAcquirer,PointAcquisitionResult,read_flag_image_fit,solve_beam_matrix)
from half_linac.src.shared.beam_diagnostics.image_fit import analyze_raw_beam_image,analyze_beam_image
from functools import partial


def parameters(method='Gaussian fit'):
    return SimpleNamespace(flagImagePV='TEST:image',flag_pixel_shape=(64,64),
        flag_image_extent=(-3.2,3.2,-3.2,3.2),samples=3,settle_time=0.,sample_interval=0.,
        beam_width_method=method,flag_image_flip_y=False,roi=None,background_image=None,
        fit_vmin=None,flag_name='PRF03')


def image(width=4.):
    a=np.arange(64)+.5-32;x,y=np.meshgrid(a,a)
    return 1000*np.exp(-(x*x+y*y)/(2*width**2))


class MeasurementServiceTests(unittest.TestCase):
    def test_all_width_methods_match_existing_image_analysis(self):
        for method in ('Gaussian fit','RMS moments','Robust RMS moments'):
            with self.subTest(method=method):
                p=parameters(method);raw=image().ravel()
                actual=read_flag_image_fit(p.flagImagePV,p.flag_pixel_shape,p.flag_image_extent,
                    method=method,full_frame_for_roi=True,get=lambda pv:raw)
                expected=analyze_raw_beam_image(raw,pixel_shape=p.flag_pixel_shape,extent=p.flag_image_extent,
                    full_frame_for_roi=True,analyzer=partial(analyze_beam_image,method=method,fit_vmin=None))
                self.assertAlmostEqual(actual[1].sigx_mm,expected[1].sigx_mm,places=14)
                self.assertAlmostEqual(actual[1].sigy_mm,expected[1].sigy_mm,places=14)

    def test_timed_point_uses_selected_method_and_records_empirical_sem(self):
        frames=iter([image(w).ravel() for w in (4.,4.1,4.2)])
        moves=[]
        with TemporaryDirectory() as d:
            acq=PointAcquirer(MeasurementSnapshot.from_parameters(parameters('RMS moments')),
                move=lambda k,check:moves.append(k),check=lambda:None,get=lambda pv:next(frames),archive_dir=d)
            point=acq.acquire_point(.2)
            observation=point.observation('x')
            self.assertEqual(moves,[.2])
            self.assertEqual(observation.metadata['freshness'],'timed_unverified')
            self.assertEqual(observation.metadata['method'],'RMS moments')
            values=[f['sigx']**2 for f in point.frames]
            self.assertAlmostEqual(observation.variance_mm2,np.mean(values))
            self.assertAlmostEqual(observation.noise_std_mm2,np.std(values,ddof=1)/np.sqrt(3))
            self.assertEqual(len(list(Path(d).glob('*.npz'))),3)

    def test_insufficient_and_zero_uncertainty_fail_without_noise_floor(self):
        quality={'x':{'usable':True},'y':{'usable':True}}
        for count in (1,3):
            result=PointAcquisitionResult(0.,tuple({'sigx':1.,'sigy':1.,'quality':quality} for _ in range(count)),'Gaussian fit')
            with self.assertRaises(ValueError):result.observation('x')

    def test_legacy_solver_keeps_its_normalization_and_raw_precision(self):
        k=np.array([-1.,0.,1.]);A=np.column_stack((np.ones(3),2*k,k*k));m=np.array([.16,.01,.09])
        result=solve_beam_matrix(A,k,A@m,114.15)
        self.assertEqual(result.status,'valid')
        self.assertAlmostEqual(result.exn_raw,np.sqrt(m[0]*m[2]-m[1]**2)*114.15e6/510998.95)
        bad=solve_beam_matrix(A,k,[1.,-1.,1.],114.15)
        self.assertNotEqual(bad.status,'valid')

    def test_cancel_during_motion_and_sampling_propagates(self):
        calls=[]
        def check():
            calls.append(True)
            if len(calls)>4:raise RuntimeError('stop')
        acq=PointAcquirer(MeasurementSnapshot.from_parameters(parameters()),
            move=lambda k,c:c(),check=check,get=lambda pv:image().ravel())
        with self.assertRaisesRegex(RuntimeError,'stop'):acq.acquire_point(0.)
