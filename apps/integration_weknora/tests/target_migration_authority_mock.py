"""Controlled unknown-ACK transport; source authority is exercised in real NC."""
import json
from http.server import HTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
events=[]
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,status,body):
  raw=json.dumps(body).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def do_GET(self):
  if self.path=='/stats':self.send(200,{'events':events});return
  events.append({'method':'GET','path':urlparse(self.path).path,'actor':parse_qs(urlparse(self.path).query).get('actor_object_guid',[''])[0]})
  self.send(404,{'error':'operation_not_found'})
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))));events.append({'method':'POST','path':self.path,'actor':body.get('actor_object_guid','')})
  if self.path.endswith('/operations') and body.get('actor_object_guid')=='780a4363-44d9-4101-a7d1-461cb64c7c54':
   self.send(200,{'operation':{**body,'knowledge_base_id':'authority-new-'+body['operation_id']}})
  else:self.send(503,{'error':'controlled_ack_unknown'})
HTTPServer(('0.0.0.0',8080),Handler).serve_forever()
