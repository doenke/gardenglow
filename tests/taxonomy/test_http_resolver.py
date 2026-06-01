import unittest
from unittest.mock import Mock, patch

from flask import Flask

from app.taxonomy.resolvers.base import ExternalCall
from app.taxonomy.resolvers.http import execute_external_call, fetch_json, fetch_text, get_full_debug_external_requests


class ExternalCallHttpExecutionTest(unittest.TestCase):
    def test_execute_external_call_uses_call_description_and_defaults(self):
        call = ExternalCall(catalog='gbif', url='https://example.test/api', query={'q': 'Phlox'})
        response = Mock()
        response.raise_for_status = Mock()

        with patch('app.taxonomy.resolvers.http.requests.get', return_value=response) as requests_get:
            result = execute_external_call(call, headers={'Accept': 'application/json'})

        self.assertIs(result, response)
        requests_get.assert_called_once_with(
            'https://example.test/api',
            params={'q': 'Phlox'},
            headers={'User-Agent': 'garten-taxonomy-resolver/1.0', 'Accept': 'application/json'},
            timeout=8,
        )
        response.raise_for_status.assert_called_once_with()


    def test_execute_external_call_captures_full_debug_when_enabled(self):
        call = ExternalCall(catalog='gbif', url='https://example.test/api', query={'q': 'Phlox'})
        response = Mock()
        response.status_code = 200
        response.url = 'https://example.test/api?q=Phlox'
        response.headers = {'Content-Type': 'application/json'}
        response.text = '{"usageKey":12345}'
        response.raise_for_status = Mock()
        app = Flask(__name__)
        app.config['GARDENGLOW_FULL_DEBUG'] = True

        with app.app_context(), patch('app.taxonomy.resolvers.http.requests.get', return_value=response):
            execute_external_call(call, headers={'Accept': 'application/json'}, timeout=3)
            captured = get_full_debug_external_requests()

        self.assertEqual(call.full_debug['response']['content'], '{"usageKey":12345}')
        self.assertEqual(call.full_debug['response']['status_code'], 200)
        self.assertEqual(call.full_debug['headers']['Accept'], 'application/json')
        self.assertEqual(call.full_debug['timeout'], 3)
        self.assertEqual(captured[0]['response']['content'], '{"usageKey":12345}')

    def test_execute_external_call_omits_full_debug_by_default(self):
        call = ExternalCall(catalog='gbif', url='https://example.test/api', query={'q': 'Phlox'})
        response = Mock()
        response.raise_for_status = Mock()
        app = Flask(__name__)
        app.config['GARDENGLOW_FULL_DEBUG'] = False

        with app.app_context(), patch('app.taxonomy.resolvers.http.requests.get', return_value=response):
            execute_external_call(call)
            captured = get_full_debug_external_requests()

        self.assertIsNone(call.full_debug)
        self.assertEqual(captured, [])

    def test_fetch_json_and_text_delegate_to_execute_external_call(self):
        call = ExternalCall(catalog='html_search', url='https://example.test/search', query={'q': 'Phlox'})
        json_response = Mock(content=b'{"ok": true}')
        json_response.json.return_value = {'ok': True}
        text_response = Mock(text='<html></html>')

        with patch('app.taxonomy.resolvers.http.execute_external_call', side_effect=[json_response, text_response]) as execute:
            self.assertEqual(fetch_json(call), {'ok': True})
            self.assertEqual(fetch_text(call), '<html></html>')

        self.assertEqual(execute.call_args_list[0].kwargs['headers'], {'Accept': 'application/json'})
        self.assertEqual(execute.call_args_list[1].kwargs['headers'], {'Accept': 'text/html,application/xhtml+xml'})


if __name__ == '__main__':
    unittest.main()
