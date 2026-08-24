"""Offline preparation tools that produce map data.

These are operator scripts, not library code: each one needs a live CARLA
server, and ``carla_calibrate`` drives several full tours of a town. They sit
at the top of the dependency stack alongside ``experiment`` -- they may import
anything, and nothing imports them. That is what keeps ``src/map`` a leaf
package whose provider and tests need no server and no ``carla`` wheel.
"""
