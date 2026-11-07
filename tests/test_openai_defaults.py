"""The official starter resolves missing choices without replacing saved ones."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import TestCase
from unittest.mock import patch

from regular import capabilities, decision_settings, instance_settings, openai_defaults
from test_instance_settings import configuration


class OfficialStarterTest(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = configuration(self.root)
        self.cfg.DECISION_ENABLED = True
        self.cfg.PROVIDER_PRESETS['openai'] = {
            'base_url': openai_defaults.BASE_URL, 'models': list(openai_defaults.CHAT_MODELS)}
        self.env = self.root / '.env'
        self.env.write_text('OPENAI_API_KEY=sk-synthetic-starter\n')

    def test_only_key_uses_official_chat_and_shared_judgment_without_extra_files(self):
        before = self.env.read_bytes()
        chat = instance_settings.public_settings(self.cfg)
        self.assertEqual(chat['active_provider'], 'openai')
        self.assertEqual(chat['model_options'], list(openai_defaults.CHAT_MODELS))
        self.assertEqual(chat['base_url'], openai_defaults.BASE_URL)
        self.assertNotIn('回复模型', chat['missing'])
        judge = decision_settings.effective_settings(self.cfg)
        self.assertEqual((judge['model'], judge['credential'], judge['api_key']),
                         ('gpt-6-luna', 'shared', ''))
        self.assertTrue(judge['automatic'])
        public = decision_settings.effective_public_settings(self.cfg)
        self.assertTrue(public['key_configured'])
        self.assertNotIn('sk-synthetic-starter', json.dumps(public))
        self.assertEqual(self.env.read_bytes(), before)
        self.assertFalse((self.root / 'data').exists())

    def test_blank_first_run_template_fields_still_show_the_starter(self):
        self.env.write_text('OPENAI_API_KEY=sk-synthetic-starter\nOPENAI_BASE_URL=\nOPENAI_MODELS=\n')
        view = instance_settings.public_settings(self.cfg)
        self.assertEqual(view['primary_model'], 'gpt-6-luna')
        self.assertEqual(view['base_url'], openai_defaults.BASE_URL)
        self.assertTrue(decision_settings.effective_public_settings(self.cfg)['key_configured'])

    def test_saved_choices_off_flags_and_independent_credentials_are_retained(self):
        self.env.write_text('AI_PROVIDER=zhipu\nCHAT_PROVIDER=zhipu\nOPENAI_API_KEY=sk-synthetic-starter\n'
                            'OPENAI_MODELS=my-model,my-backup\nVISION_ENABLED=false\n'
                            'IMAGE_GENERATION_ENABLED=false\nVOICE_OUTPUT_ENABLED=false\n')
        self.assertEqual(instance_settings.public_settings(self.cfg)['active_provider'], 'zhipu')
        self.assertEqual(instance_settings.public_settings(self.cfg, 'openai')['primary_model'], 'my-model')
        for flag in ('vision', 'image', 'voice_output'):
            self.assertFalse(capabilities.enabled(self.cfg, flag))
        decision_settings.save_settings(self.cfg.DATA_DIR, endpoint='https://judgment.example/v1/decisions',
                                        model='chosen-judge', api_key='sk-dedicated-synthetic')
        row = decision_settings.effective_settings(self.cfg)
        self.assertEqual((row['model'], row['api_key'], row['credential']),
                         ('chosen-judge', 'sk-dedicated-synthetic', 'dedicated'))
        self.assertFalse(row.get('automatic'))

    def test_compatible_service_does_not_get_official_defaults_or_judgment(self):
        self.env.write_text('OPENAI_API_KEY=sk-synthetic-gateway\nOPENAI_BASE_URL=https://gateway.example/v1\n')
        self.assertEqual(instance_settings.public_settings(self.cfg)['model_options'], [])
        self.assertIsNone(decision_settings.effective_settings(self.cfg))
        self.assertFalse(decision_settings.effective_public_settings(self.cfg)['key_configured'])

    def test_default_https_port_is_the_same_official_service(self):
        self.env.write_text('OPENAI_API_KEY=sk-synthetic-starter\nOPENAI_BASE_URL=https://API.OPENAI.COM:443/v1/\n')
        self.assertTrue(decision_settings.effective_public_settings(self.cfg)['key_configured'])
        self.assertFalse(capabilities.same_endpoint('https://api.openai.com:8443/v1', openai_defaults.BASE_URL))

    def test_backup_uses_the_registered_reasoning_model_request_shape(self):
        import config
        import providers
        with patch.dict(config.TIER_REASONING, {'chat': 'low'}):
            parameters = providers._request_params('gpt-6.1-terra', 'chat', 300, None, {})
        self.assertEqual(parameters['max_completion_tokens'], config.REASONING_MAX_TOKENS)
        self.assertNotIn('max_tokens', parameters)
        self.assertNotIn('temperature', parameters)
        self.assertEqual(parameters['extra_body'], {'reasoning_effort': 'low'})

    def test_disabled_and_damaged_judgment_are_not_replaced(self):
        with patch.object(self.cfg, 'DECISION_ENABLED', False):
            self.assertIsNone(decision_settings.effective_settings(self.cfg))
        directory = Path(self.cfg.DATA_DIR)
        directory.mkdir()
        path = directory / decision_settings.FILENAME
        path.write_text('{broken')
        self.assertIsNone(decision_settings.effective_settings(self.cfg))
        self.assertEqual(path.read_text(), '{broken')

    def test_explicit_empty_environment_key_does_not_reuse_a_stale_preset(self):
        self.cfg.PROVIDER_PRESETS['openai']['api_key'] = 'sk-stale-synthetic'
        self.cfg.EXTERNAL_ENV_KEYS = frozenset({'OPENAI_API_KEY'})
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            self.assertEqual(capabilities.openai_key(self.cfg), '')
            self.assertIsNone(decision_settings.effective_settings(self.cfg))

    def test_starter_does_not_claim_local_voice_dependencies_exist(self):
        voice = capabilities.public_settings(self.cfg)['voice_output']
        self.assertFalse(voice['enabled'])
        self.assertFalse(voice['configured'])
        self.assertIn('本机 Node', voice['missing'])
        self.assertIn('SILK 编码器', voice['missing'])

    def test_paid_preview_reports_automatic_decision_destination(self):
        from regular import eval as evaluation
        case = {'id': 'synthetic-starter-plan', 'depth': 'triage', 'checks': []}
        text = '\n'.join(evaluation.describe_traffic(self.cfg, [case], n=1))
        self.assertIn('Decisions：gpt-6-luna @ api.openai.com', text)
        self.assertNotIn('sk-synthetic-starter', text)

    def test_actual_init_then_only_key_save_works_before_and_after_restart(self):
        source = Path(__file__).resolve().parent.parent
        home = self.root / 'new-install'
        env = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(source)}
        subprocess.run([sys.executable, '-m', 'regular', '--directory', str(home), 'init'],
                       env=env, capture_output=True, text=True, check=True)
        template = (home / '.env').read_text()
        self.assertIn('OPENAI_BASE_URL=""', template)
        self.assertIn('OPENAI_MODELS=""', template)
        script = '''
import json,sys
from regular.cli import load_config
from regular import decision_settings,instance_settings
cfg=load_config(sys.argv[1])
if sys.argv[2]=='save':
    instance_settings.write_values(cfg, {'OPENAI_API_KEY':'sk-synthetic-starter',
                                        'OPENAI_BASE_URL':'https://api.openai.com/v1'})
view=instance_settings.public_settings(cfg)
print(json.dumps({'provider':view['active_provider'],'models':view['model_options'],
                  'runtime_models':cfg.CANDIDATE_MODELS,
                  'judgment':decision_settings.effective_public_settings(cfg)}))
'''
        for mode in ('save', 'restart'):
            output = subprocess.run([sys.executable, '-c', script, str(home), mode],
                                    env=env, capture_output=True, text=True, check=True)
            view = json.loads(output.stdout)
            self.assertEqual(view['provider'], 'openai')
            self.assertEqual(view['models'], list(openai_defaults.CHAT_MODELS))
            self.assertEqual(view['runtime_models'], list(openai_defaults.CHAT_MODELS))
            self.assertTrue(view['judgment']['key_configured'])
            self.assertTrue(view['judgment']['automatic'])
        self.assertFalse((home / 'data' / decision_settings.FILENAME).exists())
