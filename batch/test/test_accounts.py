import aiohttp
import base64
import os
import pytest
import secrets
from hailtop.batch_client.aioclient import BatchClient

pytestmark = pytest.mark.asyncio

DOCKER_ROOT_IMAGE = os.environ.get('DOCKER_ROOT_IMAGE', 'gcr.io/hail-vdc/ubuntu:18.04')


@pytest.fixture
async def make_client():
    _bcs = []
    async def factory(project=None):
        bc = await BatchClient(project, token_file=os.environ['HAIL_TEST_TOKEN_FILE'])
        _bcs.append(bc)
        return bc

    yield factory
    for bc in _bcs:
        await bc.close()


@pytest.fixture
async def dev_client():
    bc = await BatchClient(None, token_file=os.environ['HAIL_TEST_DEV_TOKEN_FILE'])
    yield bc
    await bc.close()


@pytest.fixture(scope='module')
def get_billing_project_name():
    prefix = f'__testproject_{secrets.token_urlsafe(15)}'
    projects = []
    def get_name():
        name = f'{prefix}_{len(projects)}'
        projects.append(name)
        return name
    return get_name


@pytest.fixture
async def new_billing_project(dev_client, get_billing_project_name):
    project = get_billing_project_name()
    yield await dev_client.create_billing_project(project)

    try:
        r = await dev_client.get_billing_project(project)
    except aiohttp.ClientResponseError as e:
        assert e.status == 403, e
    else:
        assert r['status'] != 'deleted', r
        if r['status'] == 'open':
            await dev_client.close_billing_project(project)
        if r['status'] != 'deleted':
            await dev_client.delete_billing_project(project)


async def test_bad_token():
    token = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii')
    bc = await BatchClient('test', _token=token)
    try:
        b = bc.create_batch()
        j = b.create_job(DOCKER_ROOT_IMAGE, ['false'])
        await b.submit()
        assert False, j
    except aiohttp.ClientResponseError as e:
        assert e.status == 401, e
    finally:
        await bc.close()


async def test_get_billing_project(make_client):
    c = await make_client()
    r = await c.get_billing_project('test')
    assert r['billing_project'] == 'test', r
    assert set(r['users']) == {'test', 'test-dev'}, r
    assert r['status'] == 'open', r


async def test_list_billing_projects(make_client):
    c = await make_client()
    r = await c.list_billing_projects()
    test_bps = [p for p in r if p['billing_project'] == 'test']
    assert len(test_bps) == 1, r
    bp = test_bps[0]
    assert bp['billing_project'] == 'test', bp
    assert set(bp['users']) == {'test', 'test-dev'}, bp
    assert bp['status'] == 'open', bp


async def test_unauthorized_billing_project_modification(make_client, new_billing_project):
    project = new_billing_project
    client = await make_client()
    try:
        await client.create_billing_project(project)
    except aiohttp.ClientResponseError as e:
        assert e.status == 401, e
    else:
        assert False, 'expected error'

    try:
        await client.add_user('test', project)
    except aiohttp.ClientResponseError as e:
        assert e.status == 401, e
    else:
        assert False, 'expected error'

    try:
        await client.remove_user('test', project)
    except aiohttp.ClientResponseError as e:
        assert e.status == 401, e
    else:
        assert False, 'expected error'

    try:
        await client.close_billing_project(project)
    except aiohttp.ClientResponseError as e:
        assert e.status == 401, e
    else:
        assert False, 'expected error'

    try:
        await client.reopen_billing_project(project)
    except aiohttp.ClientResponseError as e:
        assert e.status == 401, e
    else:
        assert False, 'expected error'


async def test_create_billing_project(dev_client, new_billing_project):
    project = new_billing_project
    r = await dev_client.list_billing_projects()
    assert project in {bp['billing_project'] for bp in r}

    try:
        await dev_client.create_billing_project(project)
    except aiohttp.ClientResponseError as e:
        assert e.status == 403, e
    else:
        assert False, 'expected error'


async def test_close_reopen_billing_project(dev_client, new_billing_project):
    project = new_billing_project
    await dev_client.close_billing_project(project)
    r = await dev_client.list_billing_projects()
    assert [bp for bp in r if bp['billing_project'] == project and bp['status'] == 'open'] == [], r

    try:
        await dev_client.close_billing_project(project)
    except aiohttp.ClientResponseError as e:
        assert e.status == 403, e

    await dev_client.reopen_billing_project(project)
    r = await dev_client.list_billing_projects()
    assert [bp['billing_project'] for bp in r if bp['billing_project'] == project and bp['status'] == 'open'] == [project], r

    try:
        await dev_client.reopen_billing_project(project)
    except aiohttp.ClientResponseError as e:
        assert e.status == 403, e
    else:
        assert False, 'expected error'


async def test_close_billing_project_with_open_batch_errors(dev_client, make_client, new_billing_project):
    project = new_billing_project
    await dev_client.add_user("test", project)
    client = await make_client(project)
    b = await client.create_batch()._create()

    try:
        await dev_client.close_billing_project(project)
    except aiohttp.ClientResponseError as e:
        assert e.status == 403, e
    else:
        assert False, 'expected error'
    await client._patch(f'/api/v1alpha/batches/{b.id}/close')


async def test_close_nonexistent_billing_project(dev_client):
    try:
        await dev_client.close_billing_project("nonexistent_project")
    except aiohttp.ClientResponseError as e:
        assert e.status == 404, e
    else:
        assert False, 'expected error'


async def test_add_user_with_nonexistent_billing_project(dev_client):
    try:
        await dev_client.add_user("test", "nonexistent_project")
    except aiohttp.ClientResponseError as e:
        assert e.status == 404, e
    else:
        assert False, 'expected error'


async def test_remove_user_with_nonexistent_billing_project(dev_client):
    try:
        await dev_client.remove_user("test", "nonexistent_project")
    except aiohttp.ClientResponseError as e:
        assert e.status == 404, e
    else:
        assert False, 'expected error'


async def test_delete_billing_project_only_when_closed(dev_client, new_billing_project):
    project = new_billing_project
    try:
        await dev_client.delete_billing_project(project)
    except aiohttp.ClientResponseError as e:
        assert e.status == 403, e
    else:
        assert False, 'expected error'

    await dev_client.close_billing_project(project)
    await dev_client.delete_billing_project(project)

    try:
        await dev_client.get_billing_project(project)
    except aiohttp.ClientResponseError as e:
        assert e.status == 403, e
    else:
        assert False, 'expected error'

    try:
        await dev_client.reopen_billing_project(project)
    except aiohttp.ClientResponseError as e:
        assert e.status == 403, e
    else:
        assert False, 'expected error'


async def test_add_and_delete_user(dev_client, new_billing_project):
    project = new_billing_project
    r = await dev_client.add_user('test', project)
    assert r['user'] == 'test'
    assert r['billing_project'] == project

    bp = await dev_client.get_billing_project(project)
    assert r['user'] in bp['users'], bp

    try:
        await dev_client.add_user('test', project)
    except aiohttp.ClientResponseError as e:
        assert e.status == 403, e
    else:
        assert False, 'expected error'

    r = await dev_client.remove_user('test', project)
    assert r['user']  == 'test'
    assert r['billing_project']  == project

    bp = await dev_client.get_billing_project(project)
    assert r['user'] not in bp['users']

    try:
        await dev_client.remove_user('test', project)
    except aiohttp.ClientResponseError as e:
        assert e.status == 404, e
    else:
        assert False, 'expected error'


async def test_edit_billing_limit_dev(dev_client, new_billing_project):
    project = new_billing_project
    r = await dev_client.add_user('test', project)
    assert r['user'] == 'test'
    assert r['billing_project'] == project

    limit = 5
    r = await dev_client.edit_billing_limit(project, limit)
    assert r['limit'] == limit
    r = await dev_client.get_billing_project(project)
    assert r['limit'] == limit

    limit = None
    r = await dev_client.edit_billing_limit(project, limit)
    assert r['limit'] is None
    r = await dev_client.get_billing_project(project)
    assert r['limit'] is None

    try:
        limit = 'foo'
        r = await dev_client.edit_billing_limit(project, limit)
    except aiohttp.ClientResponseError as e:
        assert e.status == 400, e
    else:
        r = await dev_client.get_billing_project(project)
        assert r['limit'] is None, r
        assert False, 'expected error'

    try:
        limit = -1
        r = await dev_client.edit_billing_limit(project, limit)
    except aiohttp.ClientResponseError as e:
        assert e.status == 400, e
    else:
        r = await dev_client.get_billing_project(project)
        assert r['limit'] is None, r
        assert False, 'expected error'


async def test_edit_billing_limit_nondev(make_client, dev_client, new_billing_project):
    project = new_billing_project
    r = await dev_client.add_user('test', project)
    assert r['user'] == 'test'
    assert r['billing_project'] == project

    client = await make_client(project)

    try:
        limit = 5
        await client.edit_billing_limit(project, limit)
    except aiohttp.ClientResponseError as e:
        assert e.status == 401, e
    else:
        r = await client.get_billing_project(project)
        assert r['limit'] is None, r
        assert False, 'expected error'


async def test_billing_project_accrued_costs(make_client, dev_client, new_billing_project):
    project = new_billing_project
    r = await dev_client.add_user('test', project)
    assert r['user'] == 'test'
    assert r['billing_project'] == project
    r = await dev_client.get_billing_project(project)
    assert r['limit'] is None
    assert r['accrued_cost'] == 0

    client = await make_client(project)

    def approx_equal(x, y, tolerance=1e-10):
        return abs(x - y) <= tolerance

    b1 = client.create_batch()
    j1_1 = b1.create_job(DOCKER_ROOT_IMAGE, command=['echo', 'head'])
    j1_2 = b1.create_job(DOCKER_ROOT_IMAGE, command=['echo', 'head'])
    b1 = await b1.submit()

    b2 = client.create_batch()
    j2_1 = b2.create_job(DOCKER_ROOT_IMAGE, command=['echo', 'head'])
    j2_2 = b2.create_job(DOCKER_ROOT_IMAGE, command=['echo', 'head'])
    b2 = await b2.submit()

    b1 = await b1.wait()
    b2 = await b2.wait()

    b1_expected_cost = (await j1_1.status())['cost'] + (await j1_2.status())['cost']
    assert approx_equal(b1_expected_cost, b1['cost']), (b1_expected_cost, b1['cost'])

    b2_expected_cost = (await j2_1.status())['cost'] + (await j2_2.status())['cost']
    assert approx_equal(b2_expected_cost, b2['cost']), (b2_expected_cost, b2['cost'])

    cost_by_batch = b1['cost'] + b2['cost']
    cost_by_billing_project = (await dev_client.get_billing_project(project))['accrued_cost']

    assert approx_equal(cost_by_batch, cost_by_billing_project), (cost_by_batch, cost_by_billing_project)


async def test_billing_limit_zero(make_client, dev_client, new_billing_project):
    project = new_billing_project
    r = await dev_client.add_user('test', project)
    assert r['user'] == 'test'
    assert r['billing_project'] == project
    r = await dev_client.get_billing_project(project)
    assert r['limit'] is None
    assert r['accrued_cost'] == 0

    limit = 0
    r = await dev_client.edit_billing_limit(project, limit)
    assert r['limit'] == limit

    client = await make_client(project)

    try:
        batch = client.create_batch()
        batch = await batch.submit()
    except aiohttp.ClientResponseError as e:
        assert e.status == 403 and 'has exceeded the budget' in e.message
    else:
        assert False, f'should receive a 403 Forbidden {batch.id}'


async def test_billing_limit_tiny(make_client, dev_client, new_billing_project):
    project = new_billing_project
    r = await dev_client.add_user('test', project)
    assert r['user'] == 'test'
    assert r['billing_project'] == project
    r = await dev_client.get_billing_project(project)
    assert r['limit'] is None
    assert r['accrued_cost'] == 0

    limit = 0.00001
    r = await dev_client.edit_billing_limit(project, limit)
    assert r['limit'] == limit

    client = await make_client(project)

    batch = client.create_batch()
    j1 = batch.create_job(DOCKER_ROOT_IMAGE, command=['sleep', '5'])
    j2 = batch.create_job(DOCKER_ROOT_IMAGE, command=['sleep', '5'], parents=[j1])
    j3 = batch.create_job(DOCKER_ROOT_IMAGE, command=['sleep', '5'], parents=[j2])
    j4 = batch.create_job(DOCKER_ROOT_IMAGE, command=['sleep', '5'], parents=[j3])
    j5 = batch.create_job(DOCKER_ROOT_IMAGE, command=['sleep', '5'], parents=[j4])
    j6 = batch.create_job(DOCKER_ROOT_IMAGE, command=['sleep', '5'], parents=[j5])
    j7 = batch.create_job(DOCKER_ROOT_IMAGE, command=['sleep', '5'], parents=[j6])
    j8 = batch.create_job(DOCKER_ROOT_IMAGE, command=['sleep', '5'], parents=[j7])
    j9 = batch.create_job(DOCKER_ROOT_IMAGE, command=['sleep', '5'], parents=[j8])
    j10 = batch.create_job(DOCKER_ROOT_IMAGE, command=['sleep', '5'], parents=[j9])
    batch = await batch.submit()
    batch = await batch.wait()
    assert batch['state'] == 'cancelled', batch
