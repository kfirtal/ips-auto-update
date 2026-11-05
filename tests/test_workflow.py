import json
from pathlib import Path
import tempfile
import io
import unittest
from unittest.mock import MagicMock, Mock, patch

import ipsupdate as app


class WorkflowTests(unittest.TestCase):
    def simulated_server(self, fail_update=False):
        config=app.load_config(Path(__file__).resolve().parents[1]/'config.example.json')
        paramiko=MagicMock(); requests=MagicMock()
        ssh=paramiko.SSHClient.return_value
        session=requests.Session.return_value.__enter__.return_value
        replies=[{'sid':'synthetic-session'}, {'task-id':'synthetic-update'},
                 {'tasks':[{'status':'succeeded'}]}, {'task-id':'synthetic-publish'},
                 {'tasks':[{'status':'succeeded'}]}, {}]
        def post(url, **kwargs):
            if fail_update and url.endswith('/run-ips-update'):
                raise RuntimeError('synthetic update failure')
            response=Mock(status_code=200); response.json.return_value=replies.pop(0)
            return response
        session.post.side_effect=post
        with patch.dict('sys.modules',{'requests':requests,'paramiko':paramiko}), \
             patch.dict('os.environ',{'IPS_LAB_API_KEY':'synthetic-test-value'}), \
             patch('builtins.open',return_value=io.BytesIO(b'synthetic-package')):
            if fail_update:
                with self.assertRaises(RuntimeError):
                    app.process_server(config,config['servers'][0])
            else:
                app.process_server(config,config['servers'][0])
        commands=[c.args[0].rsplit('/',1)[-1] for c in session.post.call_args_list]
        ssh.close.assert_called_once()
        paramiko.RejectPolicy.assert_called_once()
        return commands,ssh

    def test_success_waits_for_update_and_publish_before_removal(self):
        commands,ssh=self.simulated_server()
        self.assertEqual(commands,['login','run-ips-update','show-task','publish','show-task','logout'])
        ssh.open_sftp.return_value.__enter__.return_value.remove.assert_called_once()

    def test_update_failure_logs_out_without_publish_or_removal(self):
        commands,ssh=self.simulated_server(fail_update=True)
        self.assertEqual(commands,['login','run-ips-update','logout'])
        ssh.open_sftp.return_value.__enter__.return_value.remove.assert_not_called()

    def test_all_reported_tasks_must_succeed(self):
        call=Mock(side_effect=[{'tasks':[{'status':'succeeded'},{'status':'in progress'}]},
                               {'tasks':[{'status':'succeeded'},{'status':'succeeded'}]}])
        pause=Mock()
        app.wait_for_task(call,'synthetic-id',sleep=pause)
        self.assertEqual(call.call_count,2)
        pause.assert_called_once()

    def test_failed_and_unknown_statuses_stop(self):
        for status in ('failed','succeeded:',None):
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                app.wait_for_task(lambda *_:{'tasks':[{'status':status}]},'synthetic-id')

    def test_missing_tasks_do_not_pass(self):
        with self.assertRaises(RuntimeError):
            app.wait_for_task(lambda *_:{'tasks':[]},'synthetic-id')

    def test_polling_expires(self):
        with self.assertRaises(TimeoutError):
            app.wait_for_task(lambda *_:{'tasks':[{'status':'in progress'}]},
                              'synthetic-id',clock=Mock(side_effect=[0,0,0,901]),sleep=Mock())

    def test_dry_run_never_opens_network_or_reads_package(self):
        example=Path(__file__).resolve().parents[1]/'config.example.json'
        with patch('sys.argv',['ipsupdate.py','--config',str(example)]), \
             patch.object(app,'process_server') as remote, \
             patch.object(app.hashlib,'file_digest') as digest:
            self.assertEqual(app.main(),0)
            remote.assert_not_called()
            digest.assert_not_called()

    def test_digest_mismatch_prevents_remote_changes(self):
        example=Path(__file__).resolve().parents[1]/'config.example.json'
        config=json.loads(example.read_text())
        with tempfile.TemporaryDirectory() as directory:
            package=Path(directory)/'synthetic.upf'; package.write_bytes(b'synthetic-not-an-ips-package')
            config['package_path']=str(package)
            settings=Path(directory)/'config.json'; settings.write_text(json.dumps(config))
            with patch('sys.argv',['ipsupdate.py','--config',str(settings),'--execute',
                                   '--expected-sha256','0'*64]),patch.object(app,'process_server') as remote:
                self.assertEqual(app.main(),1)
                remote.assert_not_called()

    def test_api_does_not_follow_redirects_or_disable_tls(self):
        response=Mock(status_code=200); response.json.return_value={'tasks':[]}
        session=Mock(); session.post.return_value=response
        app.api_call(session,'management.example.invalid','show-task',{'task-id':'synthetic-id'})
        self.assertTrue(session.post.call_args.kwargs['verify'])
        self.assertFalse(session.post.call_args.kwargs['allow_redirects'])
        session.post.return_value.status_code=302
        with self.assertRaises(RuntimeError):
            app.api_call(session,'management.example.invalid','show-task',{})


if __name__=='__main__':
    unittest.main()
