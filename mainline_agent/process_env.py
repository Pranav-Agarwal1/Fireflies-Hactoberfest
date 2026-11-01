"""Minimal environments for local model and prerequisite processes."""
import os


def provisioning_environment(endpoint=None):
    # An allowlist prevents GH_TOKEN, enterprise tokens and unrelated application
    # secrets from leaking to installers, model downloads or the model server.
    allowed = ('PATH', 'HOME', 'USER', 'LOGNAME', 'TMPDIR', 'TEMP', 'TMP',
               'LANG', 'LC_ALL', 'TERM', 'SYSTEMROOT', 'WINDIR',
               'OLLAMA_MODELS', 'CUDA_VISIBLE_DEVICES', 'HIP_VISIBLE_DEVICES')
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment['OLLAMA_NO_CLOUD'] = '1'
    if endpoint:
        environment['OLLAMA_HOST'] = endpoint
    return environment
