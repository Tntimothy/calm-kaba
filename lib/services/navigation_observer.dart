// WHAT THIS FILE DOES
// Lets the home page notice when another page opens, so the scan
// stops (and the camera turns off) when you leave the screen.

import 'package:flutter/material.dart';

final detectionRouteObserver = RouteObserver<ModalRoute<void>>();
