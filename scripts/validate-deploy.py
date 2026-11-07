#!/usr/bin/env python3
"""Render, schema-check and validate the supported deployment compositions."""
import argparse
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('prepare_release', ROOT / 'scripts/prepare-release.py')
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)

def build(path):
    result = subprocess.run(['kustomize', 'build', str(path)], capture_output=True, check=True)
    return result.stdout, [obj for obj in yaml.safe_load_all(result.stdout) if obj]

def validate_schema(raw):
    subprocess.run(['kubeconform', '-strict', '-summary', '-kubernetes-version', '1.34.0',
        '-schema-location', 'default', '-schema-location',
        'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json',
        '-ignore-missing-schemas'], input=raw, check=True, stdout=subprocess.PIPE)

def check_stack(objects, version, platform, namespace='ctfd', event=False):
    by_id = {(obj['kind'], obj['metadata']['name']): obj for obj in objects}
    assert len(by_id) == len(objects), 'Duplicate object identity'
    assert ('Namespace', namespace) in by_id
    assert not any(obj['kind'] in ['ExternalSecret', 'ClusterSecretStore', 'Application'] for obj in objects)
    for obj in objects:
        if obj['kind'] != 'Namespace':
            assert obj['metadata'].get('namespace') == namespace
    for name, container in [('ctfd', 'ctfd'), ('database', 'mariadb'), ('cache', 'redis')]:
        spec = by_id[('Deployment', name)]['spec']
        assert spec['replicas'] == 1 and spec['strategy']['type'] == 'Recreate'
        pod = spec['template']['spec']
        assert pod['serviceAccountName'] == 'ctfd-runtime'
        assert pod['securityContext']['runAsNonRoot'] is True
        assert pod['securityContext']['seccompProfile']['type'] == 'RuntimeDefault'
        for value in pod['containers'] + pod.get('initContainers', []):
            assert value['securityContext']['allowPrivilegeEscalation'] is False
            assert value['securityContext']['capabilities']['drop'] == ['ALL']
        assert pod['containers'][0]['name'] == container
        assert by_id[('Service', name)]['spec']['selector'] == spec['selector']['matchLabels']
    app = by_id[('Deployment', 'ctfd')]['spec']['template']['spec']
    assert app['nodeSelector'] == {'kubernetes.io/arch': 'amd64'}
    expected = 'ghcr.io/open-tech-quest/ctfd-plugin:' + version
    assert app['containers'][0]['image'] == app['initContainers'][0]['image'] == expected
    assert 'CTFD_ADMIN_PASSWORD' not in {entry['name'] for entry in app['containers'][0]['env']}
    assert 'CTFD_ADMIN_PASSWORD' in {entry['name'] for entry in app['initContainers'][0]['env']}
    for name in ['ctfd-data', 'database-data', 'cache-data']:
        spec = by_id[('PersistentVolumeClaim', name)]['spec']
        assert spec['accessModes'] == ['ReadWriteOnce'] and 'storageClassName' not in spec
    config = next(obj for obj in objects if obj['kind'] == 'ConfigMap' and obj['metadata']['name'].startswith('ctfd-config-'))['data']
    assert config['CTFD_ADMIN_EMAIL'] == 'admin@example.com'
    assert config['WORKERS'] == ('8' if event else '2')
    if event:
        assert config['SQLALCHEMY_MAX_OVERFLOW'] == '0'
        assert app['containers'][0]['resources'] == {'requests': {'cpu': '4', 'memory': '1Gi'}, 'limits': {'cpu': '6', 'memory': '2Gi'}}
    if platform == 'openshift':
        assert ('Route', 'ctfd') in by_id and ('Ingress', 'ctfd') not in by_id
        binding = by_id[('RoleBinding', 'ctfd-nonroot')]
        assert binding['subjects'][0]['name'] == 'ctfd-runtime'
        # An omitted ServiceAccount subject namespace resolves to the binding's
        # namespace in the Kubernetes RBAC authorizer (also on OpenShift).
        assert binding['subjects'][0].get('namespace', namespace) == namespace
        policy = by_id[('NetworkPolicy', 'ctfd-ingress')]
        assert policy['spec']['ingress'][0]['from'][0]['namespaceSelector']['matchLabels'] == {'network.openshift.io/policy-group': 'ingress'}
    else:
        assert ('Ingress', 'ctfd') in by_id and ('Route', 'ctfd') not in by_id
        assert ('RoleBinding', 'ctfd-nonroot') not in by_id
        assert by_id[('Ingress', 'ctfd')]['spec']['tls'][0]['secretName'] == 'ctfd-tls'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-schema', action='store_true', help='Only for an offline render check')
    parser.add_argument('--release-version', help='Require this release version in every full stack')
    args = parser.parse_args()
    version = release.image_version_document((ROOT / release.KUSTOMIZATION).read_text())
    if args.release_version:
        release.version_tuple(args.release_version)
        assert version == args.release_version, 'Deployment image pin disagrees with the release version'
    count = 0
    for path in sorted((ROOT / 'deploy').rglob('kustomization.yaml')):
        if yaml.safe_load(path.read_text()).get('kind') == 'Component':
            continue
        raw, objects = build(path.parent)
        if not args.skip_schema:
            validate_schema(raw)
        if path.parent.relative_to(ROOT).as_posix() in ['deploy/overlays/kubernetes', 'deploy/overlays/openshift', 'deploy/examples/event-400']:
            platform = 'kubernetes' if path.parent.name == 'kubernetes' else 'openshift'
            check_stack(objects, version, platform, event=path.parent.name == 'event-400')
        count += 1
    # Test platform and sizing composition independently of the example namespace.
    for platform in ['kubernetes', 'openshift']:
        with tempfile.TemporaryDirectory(prefix='.ci-namespace-', dir=ROOT / 'deploy') as directory:
            path = Path(directory)
            (path / 'kustomization.yaml').write_text('apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nnamespace: quest-validation\nresources:\n  - ../overlays/' + platform + '\ncomponents:\n  - ../components/event-400\n')
            raw, objects = build(path)
            if not args.skip_schema:
                validate_schema(raw)
            check_stack(objects, version, platform, namespace='quest-validation', event=True)
            count += 1
    print(str(count) + ' deployment compositions validated; namespace, version, security and sizing checks passed.')

if __name__ == '__main__':
    main()
