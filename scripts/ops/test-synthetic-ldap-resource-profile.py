#!/usr/bin/env python3
"""Pure fresh resource-profile and frozen-state guards; no fixture is created."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('profile_fixture_test',HERE/'test-synthetic-ldap-fixture-state.py')
F=importlib.util.module_from_spec(spec);spec.loader.exec_module(F)


class FreshResourceProfileTest(unittest.TestCase):
    def test_default_preserves_existing_configuration(self):
        with tempfile.TemporaryDirectory() as scratch:
            state,config=F.sample(Path(scratch).resolve(),ui=True);before=copy.deepcopy(config)
            F.fixture.apply_resource_profile(config,'default')
            self.assertEqual(config,before)
            self.assertNotIn('resource_profile',state)

    def test_normal_trial_pins_actual_limits_without_budget_adoption(self):
        with tempfile.TemporaryDirectory() as scratch:
            directory=Path(scratch).resolve();state,config=F.sample(directory,ui=True)
            F.fixture.apply_resource_profile(config,'normal-trial');state['resource_profile']='normal-trial'
            state['compose_fingerprint']=F.fixture.compose_fingerprint(config);F.write(directory,state,config)
            self.assertEqual(F.fixture.owned_state(directory)[1],state)
            app=config['services']['wk-app']
            self.assertEqual(app['mem_limit'],4*1024**3)
            self.assertEqual(app['memswap_limit'],app['mem_limit'])
            self.assertEqual(app['cpus'],1)
            self.assertEqual(app['environment']['GOMEMLIMIT'],'3GiB')
            self.assertTrue(all(item['mem_limit']>0 and item['cpus']>0 for item in config['services'].values()))

    def test_recomputed_fingerprint_does_not_hide_budget_drift(self):
        mutations=[('wk-app','mem_limit',0),('wk-app','memswap_limit',8*1024**3),
                   ('wk-app','cpus',2),('wk-app','cpus',True),('wk-db','mem_limit',0),
                   ('mock-embedding','cpus',2),('nextcloud','mem_limit',1),('wk-app','GOMEMLIMIT','4GiB')]
        for service,key,value in mutations:
            with self.subTest(service=service,key=key),tempfile.TemporaryDirectory() as scratch:
                directory=Path(scratch).resolve();state,config=F.sample(directory,ui=True)
                F.fixture.apply_resource_profile(config,'normal-trial');state['resource_profile']='normal-trial'
                if key=='GOMEMLIMIT':config['services'][service]['environment'][key]=value
                else:config['services'][service][key]=value
                state['compose_fingerprint']=F.fixture.compose_fingerprint(config);F.write(directory,state,config)
                with self.assertRaisesRegex(RuntimeError,'budget differs'):F.fixture.owned_state(directory)

    def test_unsupported_profile_is_rejected(self):
        with tempfile.TemporaryDirectory() as scratch:
            _,config=F.sample(Path(scratch).resolve(),ui=False)
            with self.assertRaisesRegex(RuntimeError,'invalid fresh'):F.fixture.apply_resource_profile(config,'production')


if __name__=='__main__':unittest.main()
