# Ethereum Test Tools

Benchmark and test tools for Ethereum implementations

## Requirements

Python 3 and 2 is supported, but Python 3 is preferred.

Dependencies:
- [click](http://click.pocoo.org) - library for command line interface,
- [PyYAML](http://pyyaml.org) - YAML implementations for Python.

All dependencies can be installed by
    
    pip3 install --user -r requirements.txt
    
## Supported VMs

- evm from [go-ethereum](https://github.com/ethereum/go-ethereum).

## Example

1. Register `evm` without JIT.

   ```python3 testeth.py tool register evm-jit /usr/bin/evm -- --nojit```
   
2. Register `evm` with JIT.

   ```python3 testeth.py tool register evm-jit /usr/bin/evm -- --forcejit```

3. Execute [VM tests](https://github.com/ethereum/tests/tree/develop/VMTests).

   ```python3 testeth.py test <path-to-tests-repo>/VMTests```
