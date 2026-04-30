1. [ ] Proof of Concept phase
   1. [X] Implement `PlanarStreetGraph` which generates a random planar street graph to be used in place of Carla
   2. [ ] Implement `MapProvider` and its subclass (only one for now)
      1. [X] Implement `MapProvider` as an abstract class that provides an API that separates between the source of the 
      map (`PlanarStreetGraph` or Carla) and whatever method or object wants to use it to generate the graph that will be used by the algorithm
      2. [X] Implement `PlanarMapProvider` as a subclass of `MapProvider` that draws from `PlanarStreetGraph`
   3. [ ] Implement `TransitGraph`, the map graph that will be used by the bus algorithm as part of its run.
   4. [ ] Implement `TransitGraphBuilder` which generates `TransitGraph` from `MapProvider`
   5. [ ] Implement `RequestPool` which stores active passenger requests waiting to be handled.
   6. [ ] Implement `DemandGenerator` which generates passenger requests in real time.
   7. [ ] Implement `DispatchPolicy`, which determines which passenger requests to serve and how to prioritize them,
      depending on `RequestPool`, `TransitGraph` and the bus's current position.
   8. [ ] Implement `RoutePlanner` which turns demand and `DispatchPolicy` into a sequence of stops.
   9. [ ] Implement `BusAgent`
   10. [ ] Implement `SimulationBackend` which handles the simulation, this is probably an abstract library
   11. [ ] Implement `MockBackend` which inherits from `SimulationBackend`
   12. [ ] Implement `MetricsTracker`, self-explanatory
   13. [ ] Implement `SimulationRunner` which initializes everything, runs the tick loops, collects metrics and prints/saves results.