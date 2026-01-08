//Delivery Robot Testing

// Addresses for Town

var roads = [
  "Alice's House-Bob's House",   "Alice's House-Cabin",
  "Alice's House-Post Office",   "Bob's House-Town Hall",
  "Daria's House-Ernie's House", "Daria's House-Town Hall",
  "Ernie's House-Grete's House", "Grete's House-Farm",
  "Grete's House-Shop",          "Marketplace-Farm",
  "Marketplace-Post Office",     "Marketplace-Shop",
  "Marketplace-Town Hall",       "Shop-Town Hall"
];

// This function builds a graph between the Addresses of the town

function buildGraph(edges) {
  let graph = Object.create(null);
  function addEdge(from, to) {
    if (from in graph) {
      graph[from].push(to);
    } else {
      graph[from] = [to];
    }
  }
  for (let [from, to] of edges.map(r => r.split("-"))) {
    addEdge(from, to);
    addEdge(to, from);
  }
  return graph;
}


// Create Graph and assign it to roadGraph
var roadGraph = buildGraph(roads);


// Create VillageState and its class with the move method
// the move method checks if there is a road going from the current place
// to the destination, if there isn't, it stays at the current place
// map moves the parcels to the new place
// filter delivers the parcels at the new place and returns the the new state

var VillageState = class VillageState {
  constructor(place, parcels) {
    this.place = place;
    this.parcels = parcels;
  }

  move(destination) {
    if (!roadGraph[this.place].includes(destination)) {
      return this;
    } else {
      let parcels = this.parcels.map(p => {
        if (p.place != this.place) return p;
        return {place: destination, address: p.address};
      }).filter(p => p.place != p.address);
      return new VillageState(destination, parcels);
    }
  }
}


// This function takes in the initial state, the type of robot we are testing,
// and the memory of the function (initially set to empty [])

function runRobot(state, robot, memory) {
  for (let turn = 0;; turn++) {
    if (state.parcels.length == 0) {
      console.log(`Done in ${turn} turns`);
      break;
    }
    let action = robot(state, memory);
    state = state.move(action.direction);
    memory = action.memory;
    console.log(`Moved to ${action.direction}`);
  }
}


//This function returns a random index of the array

function randomPick(array) {
  let choice = Math.floor(Math.random() * array.length);
  return array[choice];
}


//This is the most basic robot, it is the one that randomly goes from 
//place to place, so it does not need to remember anything

function randomRobot(state) {
  return {direction: randomPick(roadGraph[state.place])};
}


//This a new random state for the Village with some parcels
// calls randomPick to get an address
// the do loop picks a new place if the same place is chosen for the new place
// that is the same as the current place

VillageState.random = function(parcelCount = 5) {
  let parcels = [];
  for (let i = 0; i < parcelCount; i++) {
    let address = randomPick(Object.keys(roadGraph));
    let place;
    do {
      place = randomPick(Object.keys(roadGraph));
    } while (place == address);
    parcels.push({place, address});
  }
  return new VillageState("Post Office", parcels);
};

//Run the Robot, this case the random robot, there's no need for the memory here
//console.log("Random Robot")
//runRobot(VillageState.random(), randomRobot);

//Robot2: The Route Robot

//This is a route for the Robot, it passes through every address, but can do it more than
//once

var mailRoute = [
  "Alice's House", "Cabin", "Alice's House", "Bob's House",
  "Town Hall", "Daria's House", "Ernie's House",
  "Grete's House", "Shop", "Grete's House", "Farm",
  "Marketplace", "Post Office"
];

//This is the route Robot, it takes initial state, it also maintains a memory of the 
//route and it removes the address from the mail route.

function routeRobot(state, memory) {
  if (memory.length == 0) {
    memory = mailRoute;
  }
  return {direction: memory[0], memory: memory.slice(1)};
}

//Run the Robot, this case the route robot, include the empty list for the memory
//console.log(" ")
//console.log("Route Robot")
//runRobot(VillageState.random(), routeRobot, []);

// Robot 3: Goal Oriented Robot

// This function tries to find the shortest route in the graph that reaches every
// address, its looks at multiple paths and choses the shortest route 

function findRoute(graph, from, to) {
  let work = [{at: from, route: []}];
  for (let i = 0; i < work.length; i++) {
    let {at, route} = work[i];
    for (let place of graph[at]) {
      if (place == to) return route.concat(place);
      if (!work.some(w => w.at == place)) {
        work.push({at: place, route: route.concat(place)});
      }
    }
  }
}

//This function checks to see if the parcel on the route, if the place has no parcel
//it picks a new route to the parcels place, else it gets the address for the parcel

function goalOrientedRobot({place, parcels}, route) {
  if (route.length == 0) {
    let parcel = parcels[0];
    if (parcel.place != place) {
      route = findRoute(roadGraph, place, parcel.place);
    } else {
      route = findRoute(roadGraph, place, parcel.address);
    }
  }
  return {direction: route[0], memory: route.slice(1)};
}

//Run the Robot, this case the goal robot, include the empty list for the memory
//console.log(" ")
//console.log("Goal Robot")
//runRobot(VillageState.random(), goalOrientedRobot, []);

//The Lazy Robot, the robot gets a reward from picking up a package, but a penalty from 
// the length of the route to get to the package, so it looks for the maximum reward

function lazyRobot({place, parcels}, route) {
  if (route.length == 0) {
    // Describe a route for every parcel
    let routes = parcels.map(parcel => {
      if (parcel.place != place) {
        return {route: findRoute(roadGraph, place, parcel.place),
                pickUp: true};
      } else {
        return {route: findRoute(roadGraph, place, parcel.address),
                pickUp: false};
      }
    });

    // This determines the precedence a route gets when choosing.
    function score({route, pickUp}) {
      return (pickUp ? 0.5 : 0) - route.length;
    }
    route = routes.reduce((a, b) => score(a) > score(b) ? a : b).route;
  }

  return {direction: route[0], memory: route.slice(1)};
}

console.log("Lazy Robot Path");
runRobot(VillageState.random(), lazyRobot, []);
console.log(" ");

// Step 5: Compare the robots

// Count the steps function, it counts the number of steps it takes for each
//run of the state, I.e. the random positioning of parcels and addresses

function countSteps(state, robot, memory) {
  for (let steps = 0;; steps++) {
    if (state.parcels.length == 0) return steps;
    let action = robot(state, memory);
    state = state.move(action.direction);
    memory = action.memory;
  }
}

// Compare the robots, this function takes in two robots, and sets their memory
//It also sets the state of the village with the VillageState random

function compareRobots(robot1, memory1, robot2, memory2, robot3, memory3, robot4, memory4) {
	let total1 = 0;
	let total2 = 0;
	let total3 = 0;
	let total4 = 0;
	for (let i = 0; i < 100; i++) {
	  let state = VillageState.random();
	  total1 += countSteps(state, robot1, memory1);
	  total2 += countSteps(state, robot2, memory2);
	  total3 += countSteps(state, robot3, memory3);
	  total4 += countSteps(state, robot4, memory4);
	}
	console.log(`Robot 1 needed ${total1 / 100} steps per task`)
	console.log(`Robot 2 needed ${total2 / 100}`)
	console.log(`Robot 3 needed ${total3 / 100}`)
	console.log(`Robot 4 needed ${total3 / 100}`)
}

compareRobots(randomRobot, [], routeRobot, [], goalOrientedRobot, [], lazyRobot, []);


