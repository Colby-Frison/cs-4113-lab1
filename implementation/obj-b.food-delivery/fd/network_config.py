"""Configuration checks shared by the regional network runners."""
import math


def validate_config(config):
    required={'restaurant_regions','restaurant_weights','kitchen_workers','couriers','kitchen_capacity','pickup_capacity','count','arrival_mean','time_scale','seed'}
    if set(config)!=required: raise ValueError('invalid network configuration keys')
    mapping=config['restaurant_regions']; couriers=config['couriers']; kitchens=config['kitchen_workers'];weights=config['restaurant_weights']
    if not mapping or not couriers or len(mapping)!=len(kitchens) or len(mapping)!=len(weights):
        raise ValueError('inconsistent restaurant configuration')
    if any(type(r) is not int or not 0<=r<len(couriers) for r in mapping) or set(mapping)!=set(range(len(couriers))):
        raise ValueError('each region must have restaurants and a valid region ID')
    if any(type(v) is not int or v<1 for v in kitchens+couriers+[config['kitchen_capacity'],config['pickup_capacity'],config['count']]):
        raise ValueError('worker counts/capacities/count must be positive integers')
    if type(config['seed']) is not int: raise ValueError('seed must be integer')
    if any(not math.isfinite(v) or v<=0 for v in weights): raise ValueError('invalid demand weights')
    if any(not math.isfinite(config[k]) or config[k]<0 for k in ('arrival_mean','time_scale')):
        raise ValueError('invalid timing configuration')
