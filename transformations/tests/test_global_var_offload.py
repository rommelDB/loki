# (C) Copyright 2018- ECMWF.
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
# In applying this licence, ECMWF does not waive the privileges and immunities
# granted to it by virtue of its status as an intergovernmental organisation
# nor does it submit to any jurisdiction.

import pytest
from pathlib import Path

from loki import (
    Scheduler, SchedulerConfig, FindNodes, Pragma, Import
)

from conftest import available_frontends
from transformations import GlobalVarOffloadTransformation


@pytest.fixture(scope='module', name='here')
def fixture_here():
    return Path(__file__).parent

@pytest.fixture(name='config')
def fixture_config():
    """
    Default configuration dict with basic options.
    """
    return {
        'default': {
            'mode': 'idem',
            'role': 'kernel',
            'expand': True,
            'strict': True,
        },
    }

@pytest.mark.parametrize('frontend', available_frontends())
def test_transformation_global_var_import(here, config, frontend):
    """
    Test the generation of offload instructions of global variable imports.
    """

    my_config = config.copy()
    my_config['default']['enable_imports'] = True
    my_config['routine'] = [
        {
            'name': 'driver',
            'role': 'driver'
        }
    ]

    scheduler = Scheduler(paths=here/'sources/projGlobalVarImports', config=my_config, frontend=frontend)
    scheduler.process(transformation=GlobalVarOffloadTransformation(), reverse=True)

    item_map = {item.name: item for item in scheduler.items}
    driver_item = item_map['#driver']
    driver = driver_item.source['driver']

    moduleA_item = item_map['modulea#var0']
    moduleA = moduleA_item.source['moduleA']
    moduleB_item = item_map['moduleb#var2']
    moduleB = moduleB_item.source['moduleB']
    moduleC_item = item_map['modulec#var4']
    moduleC = moduleC_item.source['moduleC']

    # check that global variables have been added to driver symbol table
    imports = FindNodes(Import).visit(driver.spec)
    assert len(imports) == 2
    assert imports[0].module != imports[1].module
    assert imports[0].symbols != imports[1].symbols
    for i in imports:
        assert len(i.symbols) == 2
        assert i.module.lower() in ('moduleb', 'modulec')
        assert set(s.name for s in i.symbols) in ({'var2', 'var3'}, {'var4', 'var5'})

    # check that existing acc pragmas have not been stripped and update device/update self added correctly
    pragmas = FindNodes(Pragma).visit(driver.body)
    assert len(pragmas) == 4
    assert all(p.keyword.lower() == 'acc' for p in pragmas)

    assert 'update device' in pragmas[0].content
    assert 'var2' in pragmas[0].content
    assert 'var3' in pragmas[0].content

    assert pragmas[1].content == 'serial'
    assert pragmas[2].content == 'end serial'

    assert 'update self' in pragmas[3].content
    assert 'var4' in pragmas[3].content
    assert 'var5' in pragmas[3].content

    # check that no declarations have been added for parameters
    pragmas = FindNodes(Pragma).visit(moduleA.spec)
    assert not pragmas

    # check for device-side declarations where appropriate
    pragmas = FindNodes(Pragma).visit(moduleB.spec)
    assert len(pragmas) == 2
    assert pragmas[0].content != pragmas[1].content
    assert all(p.keyword == 'acc' for p in pragmas)
    assert all('declare create' in p.content for p in pragmas)
    assert any('var2' in p.content for p in pragmas)
    assert any('var3' in p.content for p in pragmas)

    pragmas = FindNodes(Pragma).visit(moduleC.spec)
    assert len(pragmas) == 2
    assert pragmas[0].content != pragmas[1].content
    assert all(p.keyword == 'acc' for p in pragmas)
    assert all('declare create' in p.content for p in pragmas)
    assert any('var4' in p.content for p in pragmas)
    assert any('var5' in p.content for p in pragmas)
