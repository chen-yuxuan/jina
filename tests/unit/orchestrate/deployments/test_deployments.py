import json
import os

import pytest

from jina import (
    Document,
    DocumentArray,
    Executor,
    __default_executor__,
    __default_host__,
    requests,
)
from jina.clients.request import request_generator
from jina.enums import PollingType
from jina.orchestrate.deployments import Deployment
from jina.parsers import set_deployment_parser, set_gateway_parser
from jina.serve.networking import GrpcConnectionPool
from tests.unit.test_helper import MyDummyExecutor

cur_dir = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(scope='function')
def pod_args():
    args = [
        '--name',
        'test',
        '--replicas',
        '2',
        '--host',
        __default_host__,
    ]
    return set_deployment_parser().parse_args(args)


@pytest.fixture
def graph_description():
    return '{"start-gateway": ["pod0"], "pod0": ["end-gateway"]}'


@pytest.fixture(scope='function')
def pod_args_singleton():
    args = [
        '--name',
        'test2',
        '--uses-before',
        __default_executor__,
        '--replicas',
        '1',
        '--host',
        __default_host__,
    ]
    return set_deployment_parser().parse_args(args)


def test_name(pod_args):
    with Deployment(pod_args) as pod:
        assert pod.name == 'test'


def test_host(pod_args):
    with Deployment(pod_args) as pod:
        assert pod.host == __default_host__
        assert pod.head_host is None


def test_is_ready(pod_args):
    with Deployment(pod_args) as pod:
        assert pod.is_ready is True


def test_equal(pod_args, pod_args_singleton):
    pod1 = Deployment(pod_args)
    pod2 = Deployment(pod_args)
    assert pod1 == pod2
    pod1.close()
    pod2.close()
    # test not equal
    pod1 = Deployment(pod_args)
    pod2 = Deployment(pod_args_singleton)
    assert pod1 != pod2
    pod1.close()
    pod2.close()


class ChildDummyExecutor(MyDummyExecutor):
    pass


class ChildDummyExecutor2(MyDummyExecutor):
    pass


@pytest.mark.parametrize('shards', [2, 1])
def test_uses_before_after(pod_args, shards):
    pod_args.replicas = 1
    pod_args.shards = shards
    pod_args.uses_before = 'MyDummyExecutor'
    pod_args.uses_after = 'ChildDummyExecutor2'
    pod_args.uses = 'ChildDummyExecutor'
    with Deployment(pod_args) as pod:
        if shards == 2:
            assert (
                pod.head_args.uses_before_address
                == f'{pod.uses_before_args.host}:{pod.uses_before_args.port}'
            )
            assert (
                pod.head_args.uses_after_address
                == f'{pod.uses_after_args.host}:{pod.uses_after_args.port}'
            )
        else:
            assert pod.head_args is None

        assert pod.num_pods == 5 if shards == 2 else 1


def test_mermaid_str_no_secret(pod_args):
    pod_args.replicas = 3
    pod_args.shards = 3
    pod_args.uses_before = 'jinahub+docker://MyDummyExecutor:Dummy@Secret'
    pod_args.uses_after = 'ChildDummyExecutor2'
    pod_args.uses = 'jinahub://ChildDummyExecutor:Dummy@Secret'
    pod = Deployment(pod_args)
    assert 'Dummy@Secret' not in ''.join(pod._mermaid_str)


@pytest.mark.slow
@pytest.mark.parametrize('replicas', [1, 2, 4])
def test_pod_context_replicas(replicas):
    args_list = ['--replicas', str(replicas)]
    args = set_deployment_parser().parse_args(args_list)
    with Deployment(args) as bp:
        assert bp.num_pods == replicas

    Deployment(args).start().close()


@pytest.mark.slow
@pytest.mark.parametrize('shards', [1, 2, 4])
def test_pod_context_shards_replicas(shards):
    args_list = ['--replicas', str(3)]
    args_list.extend(['--shards', str(shards)])
    args = set_deployment_parser().parse_args(args_list)
    with Deployment(args) as bp:
        assert bp.num_pods == shards * 3 + 1 if shards > 1 else 3

    Deployment(args).start().close()


class AppendNameExecutor(Executor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = self.runtime_args.name

    @requests
    def foo(self, docs: DocumentArray, **kwargs):
        docs.append(Document(text=str(self.name)))
        return docs


@pytest.mark.slow
def test_pod_activates_replicas():
    args_list = ['--replicas', '3', '--shards', '2', '--disable-reduce']
    args = set_deployment_parser().parse_args(args_list)
    args.uses = 'AppendNameExecutor'
    with Deployment(args) as pod:
        assert pod.num_pods == 7
        response_texts = set()
        # replicas are used in a round robin fashion, so sending 3 requests should hit each one time
        for _ in range(6):
            response = GrpcConnectionPool.send_request_sync(
                _create_test_data_message(),
                f'{pod.head_args.host}:{pod.head_args.port}',
            )
            response_texts.update(response.response.docs.texts)
        assert 4 == len(response_texts)
        assert all(text in response_texts for text in ['0', '1', '2', 'client'])

    Deployment(args).start().close()


class AppendParamExecutor(Executor):
    def __init__(self, param, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.param = param

    @requests
    def foo(self, docs: DocumentArray, **kwargs):
        docs.append(Document(text=str(self.param)))
        return docs


async def _send_requests(pod):
    response_texts = set()
    for _ in range(3):
        response = GrpcConnectionPool.send_request_sync(
            _create_test_data_message(),
            f'{pod.head_args.host}:{pod.head_args.port}',
        )
        response_texts.update(response.response.docs.texts)
    return response_texts


class AppendShardExecutor(Executor):
    def __init__(self, runtime_args, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.shard_id = runtime_args['shard_id']

    @requests
    def foo(self, docs: DocumentArray, **kwargs):
        docs.append(Document(text=str(self.shard_id)))
        return docs


def test_pod_naming_with_shards():
    args = set_deployment_parser().parse_args(
        [
            '--name',
            'pod',
            '--shards',
            '2',
            '--replicas',
            '3',
        ]
    )
    with Deployment(args) as pod:
        assert pod.head_pod.name == 'pod/head'

        assert pod.shards[0].args[0].name == 'pod/shard-0/rep-0'
        assert pod.shards[0].args[1].name == 'pod/shard-0/rep-1'
        assert pod.shards[0].args[2].name == 'pod/shard-0/rep-2'

        assert pod.shards[1].args[0].name == 'pod/shard-1/rep-0'
        assert pod.shards[1].args[1].name == 'pod/shard-1/rep-1'
        assert pod.shards[1].args[2].name == 'pod/shard-1/rep-2'


@pytest.mark.slow
def test_pod_activates_shards():
    args_list = ['--replicas', '3']
    args_list.extend(['--shards', '3'])
    args = set_deployment_parser().parse_args(args_list)
    args.uses = 'AppendShardExecutor'
    args.polling = PollingType.ALL
    with Deployment(args) as pod:
        assert pod.num_pods == 3 * 3 + 1
        response_texts = set()
        # replicas are used in a round robin fashion, so sending 3 requests should hit each one time
        response = GrpcConnectionPool.send_request_sync(
            _create_test_data_message(),
            f'{pod.head_args.host}:{pod.head_args.port}',
        )
        response_texts.update(response.response.docs.texts)
        assert 4 == len(response.response.docs.texts)
        assert 4 == len(response_texts)
        assert all(text in response_texts for text in ['0', '1', '2', 'client'])

    Deployment(args).start().close()


@pytest.mark.slow
@pytest.mark.skipif(
    'GITHUB_WORKFLOW' in os.environ,
    reason='for unknown reason, this test is flaky on Github action, '
    'but locally it SHOULD work fine',
)
@pytest.mark.parametrize(
    'protocol, runtime_cls',
    [
        ('grpc', 'GRPCGatewayRuntime'),
    ],
)
def test_gateway_pod(protocol, runtime_cls, graph_description):
    args = set_gateway_parser().parse_args(
        [
            '--graph-description',
            graph_description,
            '--deployments-addresses',
            '{"pod0": ["0.0.0.0:1234"]}',
            '--protocol',
            protocol,
        ]
    )
    with Deployment(args) as p:
        assert len(p.all_args) == 1
        assert p.all_args[0].runtime_cls == runtime_cls

    Deployment(args).start().close()


def test_pod_naming_with_replica():
    args = set_deployment_parser().parse_args(['--name', 'pod', '--replicas', '2'])
    with Deployment(args) as bp:
        assert bp.head_pod is None
        assert bp.shards[0]._pods[0].name == 'pod/rep-0'
        assert bp.shards[0]._pods[1].name == 'pod/rep-1'


def test_pod_args_remove_uses_ba():
    args = set_deployment_parser().parse_args([])
    with Deployment(args) as p:
        assert p.num_pods == 1

    args = set_deployment_parser().parse_args(
        ['--uses-before', __default_executor__, '--uses-after', __default_executor__]
    )
    with Deployment(args) as p:
        assert p.num_pods == 1

    args = set_deployment_parser().parse_args(
        [
            '--uses-before',
            __default_executor__,
            '--uses-after',
            __default_executor__,
            '--replicas',
            '2',
        ]
    )
    with Deployment(args) as p:
        assert p.num_pods == 2


@pytest.mark.parametrize('replicas', [1])
@pytest.mark.parametrize(
    'upload_files',
    [[os.path.join(cur_dir, __file__), os.path.join(cur_dir, '__init__.py')]],
)
@pytest.mark.parametrize(
    'uses, uses_before, uses_after, py_modules, expected',
    [
        (
            os.path.join(cur_dir, '../../yaml/dummy_ext_exec.yml'),
            '',
            '',
            [
                os.path.join(cur_dir, '../../yaml/dummy_exec.py'),
                os.path.join(cur_dir, '__init__.py'),
            ],
            [
                os.path.join(cur_dir, '../../yaml/dummy_ext_exec.yml'),
                os.path.join(cur_dir, '../../yaml/dummy_exec.py'),
                os.path.join(cur_dir, __file__),
                os.path.join(cur_dir, '__init__.py'),
            ],
        ),
        (
            os.path.join(cur_dir, '../../yaml/dummy_ext_exec.yml'),
            os.path.join(cur_dir, '../../yaml/dummy_exec.py'),
            os.path.join(cur_dir, '../../yaml/dummy_ext_exec.yml'),
            [
                os.path.join(cur_dir, '../../yaml/dummy_exec.py'),
                os.path.join(cur_dir, '../../yaml/dummy_ext_exec.yml'),
            ],
            [
                os.path.join(cur_dir, '../../yaml/dummy_ext_exec.yml'),
                os.path.join(cur_dir, '../../yaml/dummy_exec.py'),
                os.path.join(cur_dir, __file__),
                os.path.join(cur_dir, '__init__.py'),
            ],
        ),
        (
            'non_existing1.yml',
            'non_existing3.yml',
            'non_existing4.yml',
            ['non_existing1.py', 'non_existing2.py'],
            [os.path.join(cur_dir, __file__), os.path.join(cur_dir, '__init__.py')],
        ),
    ],
)
def test_pod_upload_files(
    replicas,
    upload_files,
    uses,
    uses_before,
    uses_after,
    py_modules,
    expected,
):
    args = set_deployment_parser().parse_args(
        [
            '--uses',
            uses,
            '--uses-before',
            uses_before,
            '--uses-after',
            uses_after,
            '--py-modules',
            *py_modules,
            '--upload-files',
            *upload_files,
            '--replicas',
            str(replicas),
        ]
    )
    pod = Deployment(args)
    for k, v in pod.pod_args.items():
        if k in ['head', 'tail']:
            if v:
                pass
                # assert sorted(v.upload_files) == sorted(expected)
        elif v is not None and k == 'pods':
            for shard_id in v:
                for pod in v[shard_id]:
                    print(sorted(pod.upload_files))
                    print(sorted(expected))
                    assert sorted(pod.upload_files) == sorted(expected)


class DynamicPollingExecutor(Executor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @requests(on='/any')
    def any(self, docs: DocumentArray, **kwargs):
        docs.append(Document(text='added'))
        return docs

    @requests(on='/all')
    def all(self, docs: DocumentArray, **kwargs):
        docs.append(Document(text='added'))
        return docs

    @requests(on='/no_polling')
    def no_polling(self, docs: DocumentArray, **kwargs):
        docs.append(Document(text='added'))
        return docs


@pytest.mark.parametrize('polling', ['any', 'all'])
def test_dynamic_polling_with_config(polling):
    endpoint_polling = {'/any': PollingType.ANY, '/all': PollingType.ALL, '*': polling}

    args = set_deployment_parser().parse_args(
        [
            '--uses',
            'DynamicPollingExecutor',
            '--shards',
            str(2),
            '--polling',
            json.dumps(endpoint_polling),
        ]
    )
    pod = Deployment(args)

    with pod:
        response = GrpcConnectionPool.send_request_sync(
            _create_test_data_message(endpoint='/all'),
            f'{pod.head_args.host}:{pod.head_args.port}',
            endpoint='/all',
        )
        assert len(response.docs) == 1 + 2  # 1 source doc + 2 docs added by each shard

        response = GrpcConnectionPool.send_request_sync(
            _create_test_data_message(endpoint='/any'),
            f'{pod.head_args.host}:{pod.head_args.port}',
            endpoint='/any',
        )
        assert (
            len(response.docs) == 1 + 1
        )  # 1 source doc + 1 doc added by the one shard

        response = GrpcConnectionPool.send_request_sync(
            _create_test_data_message(endpoint='/no_polling'),
            f'{pod.head_args.host}:{pod.head_args.port}',
            endpoint='/no_polling',
        )
        if polling == 'any':
            assert (
                len(response.docs) == 1 + 1
            )  # 1 source doc + 1 doc added by the one shard
        else:
            assert (
                len(response.docs) == 1 + 2
            )  # 1 source doc + 1 doc added by the two shards


class DynamicPollingExecutorDefaultNames(Executor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @requests(on='/index')
    def index(self, docs: DocumentArray, **kwargs):
        docs.append(Document(text='added'))
        return docs

    @requests(on='/search')
    def search(self, docs: DocumentArray, **kwargs):
        docs.append(Document(text='added'))
        return docs


@pytest.mark.parametrize('polling', ['any', 'all'])
def test_dynamic_polling_default_config(polling):
    args = set_deployment_parser().parse_args(
        [
            '--uses',
            'DynamicPollingExecutorDefaultNames',
            '--shards',
            str(2),
            '--polling',
            polling,
        ]
    )
    pod = Deployment(args)

    with pod:
        response = GrpcConnectionPool.send_request_sync(
            _create_test_data_message(endpoint='/search'),
            f'{pod.head_args.host}:{pod.head_args.port}',
            endpoint='/search',
        )
        assert len(response.docs) == 1 + 2

        response = GrpcConnectionPool.send_request_sync(
            _create_test_data_message(endpoint='/index'),
            f'{pod.head_args.host}:{pod.head_args.port}',
            endpoint='/index',
        )
        assert len(response.docs) == 1 + 1


@pytest.mark.parametrize('polling', ['any', 'all'])
def test_dynamic_polling_overwrite_default_config(polling):
    endpoint_polling = {'/search': PollingType.ANY, '*': polling}
    args = set_deployment_parser().parse_args(
        [
            '--uses',
            'DynamicPollingExecutorDefaultNames',
            '--shards',
            str(2),
            '--polling',
            json.dumps(endpoint_polling),
        ]
    )
    pod = Deployment(args)

    with pod:
        response = GrpcConnectionPool.send_request_sync(
            _create_test_data_message(endpoint='/search'),
            f'{pod.head_args.host}:{pod.head_args.port}',
            endpoint='/search',
        )
        assert (
            len(response.docs) == 1 + 1
        )  # 1 source doc + 1 doc added by the one shard

        response = GrpcConnectionPool.send_request_sync(
            _create_test_data_message(endpoint='/index'),
            f'{pod.head_args.host}:{pod.head_args.port}',
            endpoint='/index',
        )
        assert (
            len(response.docs) == 1 + 1
        )  # 1 source doc + 1 doc added by the one shard


def _create_test_data_message(endpoint='/'):
    return list(request_generator(endpoint, DocumentArray([Document(text='client')])))[
        0
    ]


@pytest.mark.parametrize('num_shards, num_replicas', [(1, 1), (1, 2), (2, 1), (3, 2)])
def test_pod_remote_pod_replicas_host(num_shards, num_replicas):
    args = set_deployment_parser().parse_args(
        [
            '--shards',
            str(num_shards),
            '--replicas',
            str(num_replicas),
            '--host',
            __default_host__,
        ]
    )
    assert args.host == __default_host__
    with Deployment(args) as pod:
        assert pod.num_pods == num_shards * num_replicas + (1 if num_shards > 1 else 0)
        pod_args = dict(pod.pod_args['pods'])
        for k, replica_args in pod_args.items():
            assert len(replica_args) == num_replicas
            for replica_arg in replica_args:
                assert replica_arg.host == __default_host__
