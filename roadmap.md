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
   6. [X] Implement `DispatchPolicy` base class and placeholder subclass
   7. [ ] Implement `BusAgent`
   8. [ ] Implement `SimulationBackend` which handles the simulation, this is probably an abstract library
   9. [ ] Implement `MockBackend` which inherits from `SimulationBackend`
   10. [ ] Implement `MetricsTracker`, self-explanatory
   11. [ ] Implement `SimulationRunner` which initializes everything, runs the tick loops, collects metrics and prints/saves results.
   12. [ ] Implement fixed-route `DispatchPolicy`
   13. [ ] Implement at least one heuristic-based `DispatchPolicy`