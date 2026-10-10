"""Regression for the eight expired keep-alive connections after a long pause."""
import http.client,http.server,importlib.util,pathlib,socket,threading,unittest

spec=importlib.util.spec_from_file_location('pressure',pathlib.Path(__file__).with_name('isolated-source-pressure.py'))
pressure=importlib.util.module_from_spec(spec);spec.loader.exec_module(pressure)

class RetryTests(unittest.TestCase):
    def test_signed_safe_get_reconnects_with_a_distinct_nonce_and_records_failure(self):
        observations=[]
        class ExpiredConnection(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                observations.append((self.path,self.headers['X-WeKnora-Nonce']))
                if len(observations)==1:
                    self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
                self.send_response(200);self.send_header('Content-Length','3');self.end_headers();self.wfile.write(b'ok!')
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),ExpiredConnection)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        original=pressure.connection
        def local_connection():
            if not hasattr(pressure._local,'conn'):
                pressure._local.conn=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=2)
            return pressure._local.conn
        pressure.connection=local_connection
        try:
            retries=[]
            response=pressure.signed_get('/immutable-file?content_revision=3',{'token':'synthetic-transport-token','key_id':'test'},
                {'If-Match':'"immutable-etag"'},retries)
            self.assertEqual(response.status,200);self.assertEqual(response.read(),b'ok!')
            self.assertEqual(len(observations),2);self.assertEqual(observations[0][0],observations[1][0])
            self.assertNotEqual(observations[0][1],observations[1][1])
            self.assertEqual(len(retries),1);self.assertEqual(retries[0]['first_error_type'],'RemoteDisconnected')
            self.assertTrue(retries[0]['retry_response_received'])
        finally:
            if hasattr(pressure._local,'conn'): pressure._local.conn.close();del pressure._local.conn
            pressure.connection=original;server.shutdown();server.server_close();thread.join(timeout=3)

if __name__=='__main__': unittest.main()
