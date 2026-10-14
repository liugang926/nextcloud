import logging
import os
from samba.auth import system_session
from samba.provision import provision
from samba.dsdb import DS_DOMAIN_FUNCTION_2008_R2

logging.basicConfig(level=logging.WARNING)
if os.environ.get('WEKNORA_PRD_TEST_RUNTIME') != '1':
    raise SystemExit('Disposable test runtime opt-in required')
provision(logging.getLogger("prd"), system_session(), realm="PRD.EXAMPLE.TEST",
          domain="PRD", hostname="prd-ad", serverrole="active directory domain controller",
          dns_backend="SAMBA_INTERNAL", adminpass="SyntheticPrdAdmin2026!",
          use_rfc2307=True, skip_sysvolacl=True, dom_for_fun_level=DS_DOMAIN_FUNCTION_2008_R2)
