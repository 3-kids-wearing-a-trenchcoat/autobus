1. [ ] Proof of Concept phase
   1. [X] Implement `PlanarStreetGraph` which generates a random planar street graph to be used in place of Carla
   2. [X] Implement `MapProvider` and its subclass (only one for now)
      1. [X] Implement `MapProvider` as an abstract class that provides an API that separates between the source of the 
      map (`PlanarStreetGraph` or Carla) and whatever method or object wants to use it to generate the graph that will be used by the algorithm
      2. [X] Implement `PlanarMapProvider` as a subclass of `MapProvider` that draws from `PlanarStreetGraph`
   3. [X] Implement `TransitGraph`, the map graph that will be used by the bus algorithm as part of its run.
      1. [X] `TransitGraph`'s constructor should create an edge-node directed graph from `MapProvider` data.
      2. [X] `TransitGraph` methods (to be determined)
   4. [X] Implement `RequestPool` which stores active passenger requests waiting to be handled.
   5. [X] Implement `DemandGenerator` which generates passenger requests in real time.
   6. [ ] Implement `DispatchPolicy`, which determines which passenger requests to serve and how to prioritize them,
      depending on `RequestPool`, `TransitGraph` and the bus's current position.
   7. [ ] Implement `RoutePlanner` which turns demand and `DispatchPolicy` into a sequence of stops.
   8. [ ] Implement `BusAgent`
   9. [ ] Implement `SimulationBackend` which handles the simulation, this is probably an abstract library
   10. [ ] Implement `MockBackend` which inherits from `SimulationBackend`
   11. [ ] Implement `MetricsTracker`, self-explanatory
   12. [ ] Implement `SimulationRunner` which initializes everything, runs the tick loops, collects metrics and prints/saves results.