import {spawnSync} from 'node:child_process';
import path from 'node:path';
const python=process.env.VY_BUILD_PYTHON;
if(!python)throw Error('Operator build: set VY_BUILD_PYTHON to the Python environment with this repository and PyInstaller installed.');
const result=spawnSync(python,['-m','PyInstaller','--noconfirm','--clean','--onedir','--name','virtual-you-collector','--distpath','collector-dist','--workpath','collector-build','--specpath','collector-build',path.resolve('../src/virtual_you/hosted/collector.py')],{stdio:'inherit'});
if(result.status!==0)process.exit(result.status||1);
