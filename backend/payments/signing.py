import hashlib
import hmac
from accounts.crypto import decrypt_session_key


def pay_signature(uri, body, app_key):
    return hmac.new(app_key.encode(), (uri+'&'+body).encode(), hashlib.sha256).hexdigest()


def payment_packet(order, session, configuration):
    return {"mode":"short_series_goods", "signData":order.sign_data,
            "paySig":pay_signature('requestVirtualPayment',order.sign_data,configuration['appKey']),
            "signature":hmac.new(decrypt_session_key(session.encrypted_session_key).encode(),
                                 order.sign_data.encode(),hashlib.sha256).hexdigest()}
