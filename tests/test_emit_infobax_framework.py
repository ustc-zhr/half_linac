"""Integrated InfoBAX uses actual scanThread and timed acquisition, offline PV I/O."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('MPLCONFIGDIR','/tmp/emit-framework-test-mpl')
import sys,time,json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/apps/emit_measure'))
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from half_linac.src.apps.emit_measure import main
from half_linac.src.shared.machine_profile import load_app_context,resolve_channel
from half_linac.src.apps.emit_measure.infobax_framework import grid_for_parameters
from half_linac.src.apps.emit_measure.infobax_workflow import InfoBAXWorkflow


class Model:
    def get_line_elements(self,q,s):return ({'NAME':q,'TYPE':'QUAD','K1':'0','L':'.2'},{'NAME':s,'TYPE':'WATCH'})
    def get_map(self,q,s,k1=None,**kwargs):
        m=np.eye(6);m[0,1]=float(k1 or 0);m[2,3]=float(k1 or 0);return m


class FrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.pv_reads=[];self.pv_writes=[];self.frame=0
        self.context=load_app_context('emit_measure',machine_id='half',control_backend='real')
        self.q,self.screen='QT02','PRF07'
        self.pvs={key:resolve_channel(self.context,e,ch) for key,e,ch in (
            ('sol','SS01','current_set'),('sol_rb','SS01','current_readback'),
            ('sol2','SS02','current_set'),('sol2_rb','SS02','current_readback'),
            ('k1',self.q,'K1'),('qset',self.q,'current_set'),('qrb',self.q,'current_readback'),
            ('image',self.screen,'image'))}
        self.values={v:1. if k.startswith('sol') else 0. for k,v in self.pvs.items() if k!='image'}
        self.values[self.pvs['sol2']] = self.values[self.pvs['sol2_rb']] = 1.2
        def get(pv,*a,**kw):
            self.pv_reads.append(pv)
            if pv==self.pvs['image']:
                self.frame+=1
                k=self.values[self.pvs['k1']];current=self.values[self.pvs['sol']]
                scale=1.+.5*((current-.5)/2)**2 + .15*(self.values[self.pvs['sol2']] - 1.2)
                variance=(.08-.03*k+.06*k*k)*scale*(1+.002*np.sin(self.frame))
                a=np.linspace(-3.2,3.2,64);x,y=np.meshgrid(a,a)
                return (1000*np.exp(-x*x/(2*variance)-y*y/(2*(.07+.04*k*k)))).ravel()
            return self.values.get(pv)
        def put(pv,value,*a,**kw):
            self.pv_writes.append((pv,value));self.values[pv]=value
            if pv==self.pvs['sol']:self.values[self.pvs['sol_rb']]=value
            if pv==self.pvs['sol2']:self.values[self.pvs['sol2_rb']]=value
            if pv==self.pvs['k1']:self.values[self.pvs['qset']]=self.values[self.pvs['qrb']]=value
            return 1
        patch.object(main.epics,'caget',side_effect=get).start()
        patch.object(main.epics,'caput',side_effect=put).start()
        self.addCleanup(patch.stopall)
        with patch.dict(os.environ,HALF_LINAC_MACHINE_ID='half',HALF_LINAC_CONTROL_BACKEND='vm'):
            self.host=main.myWindow()
        self.host.beam_image_timer.stop();self.host._beam_image_auto_refresh_ready=False
        self.host.machine_type='real';self.host.app_context=self.context
        self.host._show_optimization();self.dialog=self.host.optimization_dialog
        self.page=self.dialog.infobax_page
        self.dialog.tabs.setCurrentIndex(1)
        self.page.active.setValue(0)
        self.dialog.solenoid_settle.setValue(0)
        # Freeze a small camera while keeping the real profile's PV resolution.
        paras=self.host.get_setting(show_warning=False)
        paras.quad_name=self.q;paras.flag_name=self.screen
        paras.app_context=self.context;paras.quadPV=self.pvs['k1'];paras.flagImagePV=self.pvs['image']
        paras.flag_pixel_shape=(64,64);paras.flag_image_extent=(-3.2,3.2,-3.2,3.2)
        paras.flag_image_flip_y=False;paras.flagExposurePV=None;paras.flagSigxPV=paras.flagSigyPV=None
        paras.roi=paras.background_image=None;paras.fit_vmin=None
        paras.k1_from=-1.;paras.k1_end=1.;paras.k1_steps=3;paras.k1_mode='absolute';paras.k1_unit='1/m^2'
        paras.samples=3;paras.settle_time=paras.sample_interval=0.;paras.EnergyMeV=225.
        paras.scan_strategy='grid';paras.adaptive_config=None;paras.model_line='TEST';paras.recal=False
        paras.scan_metadata={};paras.defer_measurement_prepare=True
        self.paras=paras
        def freeze(*args,**kwargs):
            value=deepcopy(paras)
            value.app_context=self.context
            return value
        patch.object(self.host,'optimization_parameters',side_effect=freeze).start()
        patch.object(self.dialog,'error',side_effect=lambda message: (_ for _ in ()).throw(AssertionError(message))).start()
        patch('half_linac.src.shared.machine_profile.build_model_backend',return_value=Model()).start()
        snap=SimpleNamespace(as_metadata=lambda:{'fields':[]},lattice_overrides={self.q:{'K1':0.}})
        patch('half_linac.src.shared.machine_profile.build_model_snapshot',return_value=snap).start()
        patch.object(main,'transfer',return_value=Model()).start()
        runtime={'runs_dir':Path(self.temp.name)}
        patch('half_linac.src.apps.emit_measure.infobax_page.resolve_app_runtime_paths',return_value=runtime).start()
        for name,row in self.dialog.variable_rows.items():
            self.dialog.variables.item(row,0).setCheckState(Qt.Checked if name=='SS01' else Qt.Unchecked)
            if name=='SS01':
                self.dialog.variables.item(row,3).setText('0');self.dialog.variables.item(row,4).setText('2')
        self.addCleanup(self.cleanup)

    def cleanup(self):
        if self.page.busy():
            self.page.stop();self.pump(lambda:not self.page.busy())
        self.dialog.fault=False;self.dialog.unlock_host();self.dialog.close();self.host.close()

    def pump(self,predicate,timeout=20):
        deadline=time.monotonic()+timeout
        while not predicate() and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.003)
        self.app.processEvents()
        self.assertTrue(predicate(),self.dialog.status.text())

    def complete(self):
        self.pump(lambda: not self.page.busy(), timeout=25)

    def start_paused(self, phase='baseline'):
        full_scan = InfoBAXWorkflow._full_scan_phase

        def pause_before_scan(workflow, candidate, scan_phase, records):
            if scan_phase == phase:
                workflow.request_pause()
                workflow.check()
            return full_scan(workflow, candidate, scan_phase, records)

        with patch.object(InfoBAXWorkflow, '_full_scan_phase', pause_before_scan):
            self.dialog.start()
            self.complete()

    def test_actual_scan_thread_full_loop_without_frame_id(self):
        self.page.active.setValue(5)
        self.dialog.start();self.complete()
        w=self.page.workflow
        self.assertIsNotNone(w,self.dialog.status.text())
        self.assertEqual(w.status,'completed',str(w.error))
        self.assertEqual(w.restoration_status,'succeeded')
        self.assertEqual(w.baseline_fit['source'],'existing_scan')
        self.assertEqual(w.verification_fit['source'],'existing_scan')
        self.assertEqual(len(w.baseline),3);self.assertEqual(len(w.verification),3)
        self.assertEqual(w.config.active_queries, 5)
        self.assertEqual(len(w.records),17)
        self.assertEqual(sum(row['phase'] == 'active' for row in w.records), 5)
        self.assertNotIn('current_candidate_count', w.payload()['config'])
        self.assertNotIn('initial_points_per_end', w.payload()['config'])
        self.assertEqual(self.values[self.pvs['sol']],1.)
        self.assertEqual(self.values[self.pvs['k1']],0.)
        self.assertFalse(any('frame_id' in pv for pv in self.pv_reads))
        for r in w.records:
            self.assertEqual(r['measurement']['metadata']['method'],'Gaussian fit')
            self.assertEqual(r['measurement']['metadata']['freshness'],'timed_unverified')
        self.assertFalse(self.host._optimization_locked)
        self.assertTrue(self.dialog.tabs.tabBar().isEnabled())

    def test_m0_one_initial_k1_per_end_resumes_from_archive(self):
        points = self.page.initial_points_per_end
        self.assertEqual(points.minimum(), 3)
        self.page.model.setCurrentIndex(self.page.model.findData('M0'))
        self.assertEqual(self.page.model.currentText(), 'Direct variance GP (M0)')
        self.assertEqual(points.minimum(), 1)
        points.setValue(1)
        self.start_paused('verification')

        workflow = self.page.workflow
        self.assertEqual(workflow.status, 'paused', workflow.error)
        self.assertEqual(workflow.config.model, 'M0')
        self.assertEqual(workflow.config.initial_points_per_end, 1)
        self.assertEqual(len(workflow.core.initial_design), 5)
        initialization = [record for record in workflow.records if record['phase'] == 'initialization']
        self.assertEqual([record['candidate_index'] for record in initialization], [0, 4])

        self.page.model.setCurrentIndex(self.page.model.findData('transport_moments'))
        self.assertEqual(points.value(), 3)
        with patch('half_linac.src.apps.emit_measure.infobax_page.QFileDialog.getOpenFileName',
                   return_value=(str(workflow.path), '')):
            self.page.load_run()
        self.assertEqual(self.page.model.currentData(), 'M0')
        self.assertEqual(self.page.model.currentText(), 'Direct variance GP (M0)')
        self.assertEqual(points.value(), 1)
        self.dialog.start()
        self.complete()
        self.assertEqual(self.page.workflow.status, 'completed', self.page.workflow.error)
        self.assertEqual(len(self.page.workflow.verification), 3)

    def test_custom_candidate_and_initialization_counts_resume_from_archive(self):
        self.paras.k1_steps = 4
        self.page.current_candidate_count.setValue(7)
        self.page.initial_points_per_end.setValue(4)
        self.start_paused('verification')

        workflow = self.page.workflow
        self.assertEqual(workflow.status, 'paused')
        self.assertEqual(len(workflow.candidates), 7)
        self.assertEqual(workflow.core.pool.query_count, 28)
        initialization = [r for r in workflow.records if r['phase'] == 'initialization']
        self.assertEqual([(r['candidate_index'], r['scan_index']) for r in initialization],
                         [(candidate, scan) for candidate in (0, 6) for scan in range(4)])

        self.page.current_candidate_count.setValue(5)
        self.page.initial_points_per_end.setValue(3)
        self.pv_writes.clear()
        self.dialog.start()
        self.pump(lambda: not self.page.busy())
        self.assertIn('settings differ', self.dialog.status.text())
        self.assertEqual(self.pv_writes, [])

        with patch('half_linac.src.apps.emit_measure.infobax_page.QFileDialog.getOpenFileName',
                   return_value=(str(workflow.path), '')):
            self.page.load_run()
        self.assertEqual(self.page.current_candidate_count.value(), 7)
        self.assertEqual(self.page.initial_points_per_end.value(), 4)
        self.dialog.start()
        self.complete()
        self.assertEqual(self.page.workflow.status, 'completed', self.page.workflow.error)

    def test_pause_before_motion_and_resume_without_confirmation(self):
        before_scan = Event()

        def wait_for_pause(workflow, *_args):
            before_scan.set()
            while not workflow.pause_requested.wait(.01):
                workflow.check()
            workflow.check()

        self.pv_writes.clear()
        with patch.object(InfoBAXWorkflow, '_full_scan_phase', wait_for_pause):
            self.dialog.start()
            self.pump(before_scan.is_set)
            self.assertEqual(self.pv_writes, [])
            self.assertFalse(self.dialog.tabs.tabBar().isEnabled())
            self.assertFalse(self.page.current_candidate_count.isEnabled())
            self.assertFalse(self.page.initial_points_per_end.isEnabled())
            self.dialog.tabs.setCurrentIndex(0)
            self.assertEqual(self.dialog.tabs.currentIndex(), 1)
            self.page.stop(pause=True)
            self.complete()
        self.assertEqual(self.page.workflow.status,'paused')
        self.assertEqual(self.page.workflow.restoration_status,'succeeded')
        self.dialog.start()
        self.complete()
        self.assertEqual(self.page.workflow.status, 'completed', self.page.workflow.error)

    def test_main_frame_count_is_validated_before_writes(self):
        self.paras.samples=1
        self.pv_writes.clear()
        with patch.object(self.dialog,'error') as error:self.dialog.start()
        error.assert_called_once();self.assertIn('Samples',error.call_args.args[0])
        self.assertEqual(self.pv_writes,[])

    def test_empty_solenoid_selection_is_rejected_before_motion(self):
        row = self.dialog.variable_rows['SS01']
        self.dialog.variables.item(row, 0).setCheckState(Qt.Unchecked)
        self.pv_writes.clear()
        with patch.object(self.dialog, 'error') as error:
            self.dialog.start()
        self.assertIn('at least one', error.call_args.args[0])
        self.assertEqual(self.pv_writes, [])

    def test_two_solenoids_resume_and_apply_the_verified_current_vector(self):
        initial = (1., 1.2)
        row = self.dialog.variable_rows['SS02']
        self.dialog.variables.item(row, 0).setCheckState(Qt.Checked)
        self.dialog.variables.item(row, 3).setText('0')
        self.dialog.variables.item(row, 4).setText('2')
        self.page.active.setValue(2)
        self.start_paused('verification')

        workflow = self.page.workflow
        self.assertEqual(workflow.status, 'paused', workflow.error)
        self.assertEqual(workflow.initial['current_a'], initial)
        self.assertIn(initial, workflow.candidates)
        self.assertEqual(workflow.core.pool.candidate_coordinates.shape, (5, 2))
        self.assertEqual(sum(record['phase'] == 'active' for record in workflow.records), 2)
        self.assertTrue(all(pv in {entry[0] for entry in self.pv_writes}
                            for pv in (self.pvs['sol'], self.pvs['sol2'])))
        self.assertEqual(tuple(self.values[self.pvs[key]] for key in ('sol', 'sol2')), initial)

        self.dialog.variables.item(row, 0).setCheckState(Qt.Unchecked)
        with patch('half_linac.src.apps.emit_measure.infobax_page.QFileDialog.getOpenFileName',
                   return_value=(str(workflow.path), '')):
            self.page.load_run()
        self.assertEqual(self.dialog.selected_names(), ['SS01', 'SS02'])
        self.assertIn('SS02=', self.page.table.item(0, 1).text())
        self.dialog.start()
        self.complete()

        workflow = self.page.workflow
        self.assertEqual(workflow.status, 'completed', workflow.error)
        self.assertEqual(workflow.restoration_status, 'succeeded')
        self.assertTrue(workflow.confirmed)
        self.assertEqual(tuple(self.values[self.pvs[key]] for key in ('sol', 'sol2')), initial)
        recommendation = workflow.candidates[workflow.recommendation['recommended_candidate_index']]
        with patch.object(main.QMessageBox, 'question', return_value=main.QMessageBox.Yes):
            self.page.manual('best')
            self.complete()
        self.assertEqual(tuple(self.values[self.pvs[key]] for key in ('sol', 'sol2')), recommendation)
        with patch('half_linac.src.apps.emit_measure.infobax_page.QFileDialog.getOpenFileName',
                   return_value=(str(workflow.path), '')):
            self.page.load_run()
        self.page.manual('initial')
        self.complete()
        self.assertTrue(self.page.workflow.machine.recovery_only)
        self.assertEqual(tuple(self.values[self.pvs[key]] for key in ('sol', 'sol2')), initial)

    def test_partial_baseline_resumes_only_missing_grid_points(self):
        from half_linac.src.apps.emit_measure.infobax_framework import FrameworkInfoBAXMachine
        original=FrameworkInfoBAXMachine.scan_parameters
        paused=[False]
        def parameters_with_pause(machine,directory,completed,on_point,lease):
            def completed_point(result):
                on_point(result)
                if not paused[0]:
                    paused[0]=True
                    self.page.worker.workflow.request_pause()
                    time.sleep(.15)
            return original(machine,directory,completed,completed_point,lease)
        with patch.object(FrameworkInfoBAXMachine,'scan_parameters',parameters_with_pause):
            self.dialog.start();self.complete()
        w=self.page.workflow
        self.assertEqual(w.status,'paused',w.error)
        self.assertEqual(len(w.baseline),1)
        first=w.baseline[0]['measurement']
        self.dialog.start();self.complete()
        w=self.page.workflow
        self.assertEqual(w.status,'completed',w.error)
        self.assertEqual(len(w.baseline),3)
        self.assertEqual(w.baseline[0]['measurement'],first)
        baseline=[r for r in w.records if r['phase']=='baseline']
        self.assertEqual(len({r['query_index'] for r in baseline}),3)
        self.assertEqual(len(baseline),3)

    def test_restore_failure_locks_then_manual_recovery_unlocks(self):
        original=main.epics.caput.side_effect
        main.epics.caput.side_effect=lambda pv,v,*a,**kw:0 if pv==self.pvs['sol'] else original(pv,v,*a,**kw)
        self.start_paused()
        self.assertTrue(self.dialog.fault)
        self.assertFalse(self.dialog.apply_button.isEnabled())
        self.assertTrue(self.dialog.restore_button.isEnabled())
        self.assertFalse(self.dialog.tabs.tabBar().isEnabled())
        main.epics.caput.side_effect=original
        self.page.manual('initial');self.pump(lambda:not self.page.busy())
        self.assertFalse(self.dialog.fault)
        self.assertFalse(self.host._optimization_locked)
        self.assertEqual(self.page.workflow.restoration_status,'succeeded')

    def test_changed_measurement_settings_reject_resume_without_writes(self):
        self.start_paused()
        self.paras.samples=4;self.pv_writes.clear()
        self.dialog.start();self.pump(lambda:not self.page.busy())
        self.assertIn('settings differ',self.dialog.status.text())
        self.assertEqual(self.pv_writes,[])

    def test_archive_recovery_does_not_require_camera_or_main_sampling_settings(self):
        self.start_paused()
        w=self.page.workflow
        saved=w.payload();saved.update(run_dir=str(w.run_dir))
        self.page.workflow=None;self.page.saved=saved
        self.paras.samples=1
        original=main.epics.caget.side_effect
        main.epics.caget.side_effect=lambda pv,*a,**kw: (_ for _ in ()).throw(AssertionError('Camera must not be read during restore')) if pv==self.pvs['image'] else original(pv,*a,**kw)
        self.page.manual('initial');self.pump(lambda:not self.page.busy())
        self.assertIsNone(self.page.error,self.dialog.status.text())
        self.assertEqual(self.page.workflow.restoration_status,'succeeded')
        self.assertTrue(self.page.workflow.machine.recovery_only)
        with self.assertRaises(RuntimeError):self.page.workflow.run()

    def test_legacy_archive_is_read_only_and_can_be_cleared(self):
        from half_linac.src.apps.emit_measure.infobax_launcher import build_workflow
        path=Path(self.temp.name)/'legacy'
        w=build_workflow({'backend':'fake','current_low':-1,'current_high':1},path)
        data=w.payload();data['schema']='half_infobax_workflow_v1'
        path.mkdir();file=path/'infobax_workflow.json';file.write_text(json.dumps(data))
        self.pv_writes.clear();before=len(self.pv_reads)
        with patch('half_linac.src.apps.emit_measure.infobax_page.QFileDialog.getOpenFileName',return_value=(str(file),'')):
            self.page.load_run()
        self.assertTrue(self.page.read_only)
        self.assertFalse(self.dialog.start_button.isEnabled())
        self.assertFalse(self.dialog.apply_button.isEnabled())
        self.assertEqual(self.pv_writes,[])
        self.assertEqual(len(self.pv_reads),before)
        self.page.clear_archive();self.assertFalse(self.page.read_only)

    def test_grid_uses_adaptive_range_without_changing_mode(self):
        self.paras.scan_strategy='adaptive_quality'
        self.paras.adaptive_config=SimpleNamespace(k1_min=-2.,k1_max=2.)
        self.assertEqual(grid_for_parameters(self.paras,0.),(-2.,0.,2.))
        self.assertEqual(self.paras.scan_strategy,'adaptive_quality')


from copy import deepcopy
if __name__=='__main__':unittest.main()
