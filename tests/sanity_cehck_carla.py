import os
import sys
import time
import traceback

try:
    import carla
except Exception as e:
    print("Failed to import carla:")
    raise

HOST = os.getenv("CARLA_HOST", "localhost")
PORT = int(os.getenv("CARLA_PORT", "2000"))
TIMEOUT = float(os.getenv("CARLA_TIMEOUT", "10.0"))

def main():
    client = carla.Client(HOST, PORT)
    client.set_timeout(TIMEOUT)

    print(f"Connecting to CARLA at {HOST}:{PORT} ...")
    print("Server version:", client.get_server_version())
    print("Client version:", client.get_client_version())

    world = client.get_world()
    print("Map:", world.get_map().name)
    print("Settings:", world.get_settings())

    blueprint_library = world.get_blueprint_library()
    vehicle_bps = blueprint_library.filter("vehicle.*")

    if not vehicle_bps:
        print("No vehicle blueprints found.")
        return 1

    spawn_points = world.get_map().get_spawn_points()
    if not spawn_points:
        print("No spawn points available on this map.")
        return 1

    vehicle_bp = vehicle_bps[0]
    spawn_point = spawn_points[0]

    print(f"Spawning test vehicle: {vehicle_bp.id}")
    vehicle = world.try_spawn_actor(vehicle_bp, spawn_point)

    if vehicle is None:
        print("Could not spawn vehicle at the first spawn point.")
        return 1

    try:
        print("Vehicle spawned successfully.")
        print("Actor ID:", vehicle.id)
        print("Location:", vehicle.get_location())

        vehicle.set_autopilot(True)
        print("Autopilot enabled for 3 seconds...")
        time.sleep(3)

        vehicle.set_autopilot(False)
        print("Autopilot disabled.")
    finally:
        print("Destroying test vehicle...")
        vehicle.destroy()

    print("Sanity check passed.")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        raise SystemExit(130)
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)