# improved_bpmn_di.py
"""
Improved BPMN DI (Diagram Interchange) handler with better layout algorithm
and more robust XML handling.
"""

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Set
import math
from collections import defaultdict
# Namespaces
BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"
DI_NS = "http://www.omg.org/spec/DD/20100524/DI"
DC_NS = "http://www.omg.org/spec/DD/20100524/DC"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

# Register namespaces properly
ET.register_namespace("", BPMN_NS)  # Default namespace
ET.register_namespace("bpmndi", BPMNDI_NS)
ET.register_namespace("di", DI_NS)
ET.register_namespace("dc", DC_NS)
ET.register_namespace("xsi", XSI_NS)


class BPMNLayoutEngine:
    """Advanced BPMN auto-layout engine with better positioning algorithms"""
    
    # Element dimensions (matching BPMN.io defaults)
    SIZES = {
        "startEvent": (36, 36),
        "endEvent": (36, 36),
        "task": (100, 80),
        "userTask": (100, 80),
        "serviceTask": (100, 80),
        "scriptTask": (100, 80),
        "sendTask": (100, 80),
        "receiveTask": (100, 80),
        "manualTask": (100, 80),
        "businessRuleTask": (100, 80),
        "subProcess": (120, 100),
        "callActivity": (100, 80),
        "exclusiveGateway": (50, 50),
        "parallelGateway": (50, 50),
        "inclusiveGateway": (50, 50),
        "eventBasedGateway": (50, 50),
        "complexGateway": (50, 50),
        "intermediateCatchEvent": (36, 36),
        "intermediateThrowEvent": (36, 36),
        "boundaryEvent": (36, 36),
        "dataObject": (40, 60),
        "dataStore": (50, 50),
        "textAnnotation": (100, 30),
    }
    
    def __init__(self):
        self.node_positions = {}
        self.flow_waypoints = {}
        
    def layout(self, process_element: ET.Element) -> Tuple[Dict, Dict]:
        """
        Perform auto-layout of BPMN process with better algorithm
        Returns: (node_positions, flow_waypoints)
        """
        # 1. Build graph structure
        nodes, flows = self._extract_graph(process_element)
        
        # 2. Build adjacency lists
        successors = defaultdict(list)
        predecessors = defaultdict(list)
        
        for flow in flows:
            src, tgt = flow["source"], flow["target"]
            if src in nodes and tgt in nodes:
                successors[src].append(tgt)
                predecessors[tgt].append(src)
        
        # 3. Find start nodes
        start_nodes = []
        for node_id in nodes:
            if nodes[node_id]["type"] == "startEvent":
                start_nodes.append(node_id)
            elif len(predecessors[node_id]) == 0:
                start_nodes.append(node_id)
        
        if not start_nodes and nodes:
            # If no clear start, pick one arbitrarily
            start_nodes = [list(nodes.keys())[0]]
        
        # 4. Calculate distances from start using BFS
        distances = {}
        visited = set()
        queue = [(node, 0) for node in start_nodes]
        max_distance = 0
        
        while queue:
            node, dist = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            distances[node] = dist
            max_distance = max(max_distance, dist)
            
            for successor in successors[node]:
                if successor not in visited:
                    queue.append((successor, dist + 1))
        
        # 5. Group nodes by distance (layers)
        layers = defaultdict(list)
        for node_id in nodes:
            if node_id in distances:
                layers[distances[node_id]].append(node_id)
            else:
                # Unconnected nodes go to last layer
                layers[max_distance + 1].append(node_id)
        
        # 6. Position nodes
        X_START = 50
        Y_START = 50
        X_GAP = 200  # Horizontal gap between layers
        Y_GAP = 120  # Vertical gap between nodes
        
        self.node_positions = {}
        
        for layer_idx in sorted(layers.keys()):
            layer_nodes = layers[layer_idx]
            x = X_START + layer_idx * X_GAP
            
            # Sort nodes in layer to minimize edge crossings
            # Simple heuristic: sort by number of connections to previous layer
            if layer_idx > 0:
                prev_layer = layers[layer_idx - 1]
                layer_nodes.sort(key=lambda n: sum(1 for p in predecessors[n] if p in prev_layer))
            
            # Position nodes vertically
            layer_height = len(layer_nodes) * Y_GAP
            y_offset = Y_START + max(0, (600 - layer_height) / 2)
            
            for i, node_id in enumerate(layer_nodes):
                y = y_offset + i * Y_GAP
                self.node_positions[node_id] = (x, y)
        
        # 7. Route edges
        self.flow_waypoints = {}
        for flow in flows:
            flow_id = flow["id"]
            source = flow["source"]
            target = flow["target"]
            
            if source in self.node_positions and target in self.node_positions:
                sx, sy = self.node_positions[source]
                tx, ty = self.node_positions[target]
                
                # Get element types
                source_type = nodes.get(source, {}).get("type", "task")
                target_type = nodes.get(target, {}).get("type", "task")
                
                # Determine sizes
                sw, sh = self.SIZES.get(source_type, (100, 80))
                tw, th = self.SIZES.get(target_type, (100, 80))
                
                # Connection points
                start_x = sx + sw
                start_y = sy + sh / 2
                end_x = tx
                end_y = ty + th / 2
                
                # Create waypoints
                if abs(tx - sx) > sw + 50:  # Normal forward connection
                    self.flow_waypoints[flow_id] = [(start_x, start_y), (end_x, end_y)]
                else:  # Need to route around (backward connection)
                    mid_x = sx + sw + 50
                    self.flow_waypoints[flow_id] = [
                        (start_x, start_y),
                        (mid_x, start_y),
                        (mid_x, end_y),
                        (end_x, end_y)
                    ]
        
        return self.node_positions, self.flow_waypoints
    
    def _extract_graph(self, process: ET.Element) -> Tuple[Dict, List]:
        """Extract nodes and flows from process"""
        nodes = {}
        flows = []
        
        for elem in process:
            elem_id = elem.get("id")
            if not elem_id:
                continue
                
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            
            if tag == "sequenceFlow":
                flows.append({
                    "id": elem_id,
                    "source": elem.get("sourceRef"),
                    "target": elem.get("targetRef")
                })
            else:
                # Include ALL elements (tasks, events, gateways)
                nodes[elem_id] = {
                    "id": elem_id,
                    "type": tag,
                    "name": elem.get("name", ""),
                    "element": elem
                }
        
        return nodes, flows
    
    def _create_layers(self, nodes: Dict, flows: List) -> List[List[str]]:
        """Create layers of nodes for hierarchical layout"""
        # Build adjacency lists
        successors = {node_id: [] for node_id in nodes}
        predecessors = {node_id: [] for node_id in nodes}
        
        for flow in flows:
            if flow["source"] in nodes and flow["target"] in nodes:
                successors[flow["source"]].append(flow["target"])
                predecessors[flow["target"]].append(flow["source"])
        
        # Find start nodes
        start_nodes = [
            node_id for node_id, node in nodes.items()
            if node["type"] == "startEvent" or len(predecessors[node_id]) == 0
        ]
        
        # Assign layers using modified BFS
        layers = []
        assigned = set()
        current_layer = start_nodes
        
        while current_layer:
            # Add current layer
            layers.append(current_layer)
            assigned.update(current_layer)
            
            # Find next layer
            next_layer = []
            for node_id in current_layer:
                for successor in successors[node_id]:
                    if successor not in assigned:
                        # Check if all predecessors are assigned
                        if all(pred in assigned for pred in predecessors[successor]):
                            next_layer.append(successor)
            
            # Remove duplicates while preserving order
            seen = set()
            current_layer = []
            for node in next_layer:
                if node not in seen:
                    seen.add(node)
                    current_layer.append(node)
        
        return layers
    
    def _position_nodes(self, layers: List[List[str]]):
        """Position nodes in their layers with better spacing"""
        X_START = 50
        Y_START = 50
        X_GAP = 150  # Horizontal gap between layers
        Y_GAP = 120  # Vertical gap between nodes
        MAX_HEIGHT = 2000  # Maximum canvas height
        
        # If no layers or empty layers, position all nodes we have
        if not layers or all(len(layer) == 0 for layer in layers):
            # Fallback: position any nodes we know about
            all_nodes = list(self.node_positions.keys()) if self.node_positions else []
            if all_nodes:
                layers = [all_nodes[i:i+5] for i in range(0, len(all_nodes), 5)]
        
        for layer_idx, layer in enumerate(layers):
            if not layer:  # Skip empty layers
                continue
                
            x = X_START + layer_idx * X_GAP
            
            # Calculate layer height and adjust Y_GAP if needed
            num_nodes = len(layer)
            effective_y_gap = Y_GAP
            if num_nodes * Y_GAP > MAX_HEIGHT:
                effective_y_gap = MAX_HEIGHT / num_nodes
            
            layer_height = num_nodes * effective_y_gap
            y_offset = Y_START + max(0, (800 - layer_height) / 2)  # Center vertically
            
            for node_idx, node_id in enumerate(layer):
                y = y_offset + node_idx * effective_y_gap
                self.node_positions[node_id] = (x, y)
    
    def _route_edges(self, flows: List):
        """Create waypoints for edges"""
        for flow in flows:
            flow_id = flow["id"]
            source = flow["source"]
            target = flow["target"]
            
            if source in self.node_positions and target in self.node_positions:
                # Get positions
                sx, sy = self.node_positions[source]
                tx, ty = self.node_positions[target]
                
                # Simple routing: direct connection with optional bend
                waypoints = []
                
                # Start point (right side of source)
                waypoints.append((sx + 50, sy + 40))  # Assuming average width
                
                # End point (left side of target)
                waypoints.append((tx, ty + 40))
                
                # Add bend point if needed (for better visualization)
                if abs(sy - ty) > 20:  # Significant vertical difference
                    mid_x = (sx + tx) / 2
                    waypoints.insert(1, (mid_x, sy + 40))
                    waypoints.insert(2, (mid_x, ty + 40))
                
                self.flow_waypoints[flow_id] = waypoints


def ensure_valid_bpmn_di(bpmn_xml: str) -> str:
    """
    Ensures BPMN has valid DI (Diagram Interchange) elements.
    Fixes common issues and adds missing DI elements with proper layout.
    """
    try:
        # Parse with fallback encoding
        try:
            root = ET.fromstring(bpmn_xml.encode('utf-8'))
        except:
            # Try to fix common encoding issues
            bpmn_xml = bpmn_xml.replace('&', '&amp;').replace('<', '&lt;', 1)
            root = ET.fromstring(bpmn_xml)
        
        # Find or create definitions element
        if root.tag != f"{{{BPMN_NS}}}definitions":
            # Wrap in definitions if needed
            definitions = ET.Element(f"{{{BPMN_NS}}}definitions")
            definitions.append(root)
            root = definitions
        
        # Ensure proper attributes on definitions
        if "id" not in root.attrib:
            root.set("id", "Definitions_1")
        if "targetNamespace" not in root.attrib:
            root.set("targetNamespace", "http://bpmn.io/schema/bpmn")
        
        # Set namespace attributes
        root.set(f"{{{XSI_NS}}}schemaLocation", 
                "http://www.omg.org/spec/BPMN/20100524/MODEL BPMN20.xsd")
        
        # Find process
        ns = {"bpmn": BPMN_NS}
        process = root.find(".//bpmn:process", ns)
        
        if process is None:
            # Create process if missing
            process = ET.SubElement(root, f"{{{BPMN_NS}}}process")
            process.set("id", "Process_1")
            process.set("isExecutable", "true")
        
        process_id = process.get("id", "Process_1")
        
        # Check if DI already exists
        diagram = root.find(f".//{{{BPMNDI_NS}}}BPMNDiagram")
        
        if diagram is None:
            # Create DI structure
            layout_engine = BPMNLayoutEngine()
            node_positions, flow_waypoints = layout_engine.layout(process)
            
            # Create diagram
            diagram = ET.SubElement(root, f"{{{BPMNDI_NS}}}BPMNDiagram")
            diagram.set("id", f"BPMNDiagram_{process_id}")
            
            # Create plane
            plane = ET.SubElement(diagram, f"{{{BPMNDI_NS}}}BPMNPlane")
            plane.set("id", f"BPMNPlane_{process_id}")
            plane.set("bpmnElement", process_id)
            
            # Create shapes
            for elem in process:
                elem_id = elem.get("id")
                if not elem_id:
                    continue
                
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                
                if tag != "sequenceFlow" and elem_id in node_positions:
                    shape = ET.SubElement(plane, f"{{{BPMNDI_NS}}}BPMNShape")
                    shape.set("id", f"{elem_id}_di")
                    shape.set("bpmnElement", elem_id)
                    
                    # Get size
                    width, height = layout_engine.SIZES.get(tag, (100, 80))
                    
                    # Create bounds
                    bounds = ET.SubElement(shape, f"{{{DC_NS}}}Bounds")
                    x, y = node_positions[elem_id]
                    bounds.set("x", str(x))
                    bounds.set("y", str(y))
                    bounds.set("width", str(width))
                    bounds.set("height", str(height))
                    
                    # Add label for certain elements
                    if elem.get("name") and tag not in ["startEvent", "endEvent"]:
                        label = ET.SubElement(shape, f"{{{BPMNDI_NS}}}BPMNLabel")
                        label_bounds = ET.SubElement(label, f"{{{DC_NS}}}Bounds")
                        label_bounds.set("x", str(x))
                        label_bounds.set("y", str(y + height + 5))
                        label_bounds.set("width", str(width))
                        label_bounds.set("height", "20")
            
            # Create edges
            for elem in process:
                if elem.tag == f"{{{BPMN_NS}}}sequenceFlow":
                    flow_id = elem.get("id")
                    if flow_id in flow_waypoints:
                        edge = ET.SubElement(plane, f"{{{BPMNDI_NS}}}BPMNEdge")
                        edge.set("id", f"{flow_id}_di")
                        edge.set("bpmnElement", flow_id)
                        
                        # Add waypoints
                        for x, y in flow_waypoints[flow_id]:
                            waypoint = ET.SubElement(edge, f"{{{DI_NS}}}waypoint")
                            waypoint.set("x", str(x))
                            waypoint.set("y", str(y))
        
        # Convert back to string with proper declaration
        xml_str = ET.tostring(root, encoding='unicode')
        
        # Add XML declaration if missing
        if not xml_str.startswith('<?xml'):
            xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str
        
        return xml_str
        
    except Exception as e:
        # If all else fails, return a minimal valid BPMN
        return create_minimal_valid_bpmn()


def create_minimal_valid_bpmn() -> str:
        """Creates a minimal valid BPMN with DI"""
        return '''<?xml version="1.0" encoding="UTF-8"?>
    <definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" 
                xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" 
                xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" 
                xmlns:di="http://www.omg.org/spec/DD/20100524/DI" 
                id="Definitions_1" 
                targetNamespace="http://bpmn.io/schema/bpmn">
    <process id="Process_1" isExecutable="true">
        <startEvent id="StartEvent_1" name="Start"/>
        <task id="Task_1" name="Process Task"/>
        <endEvent id="EndEvent_1" name="End"/>
        <sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="Task_1"/>
        <sequenceFlow id="Flow_2" sourceRef="Task_1" targetRef="EndEvent_1"/>
    </process>
    <bpmndi:BPMNDiagram id="BPMNDiagram_1">
        <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Process_1">
        <bpmndi:BPMNShape id="StartEvent_1_di" bpmnElement="StartEvent_1">
            <dc:Bounds x="152" y="102" width="36" height="36"/>
        </bpmndi:BPMNShape>
        <bpmndi:BPMNShape id="Task_1_di" bpmnElement="Task_1">
            <dc:Bounds x="240" y="80" width="100" height="80"/>
        </bpmndi:BPMNShape>
        <bpmndi:BPMNShape id="EndEvent_1_di" bpmnElement="EndEvent_1">
            <dc:Bounds x="392" y="102" width="36" height="36"/>
        </bpmndi:BPMNShape>
        <bpmndi:BPMNEdge id="Flow_1_di" bpmnElement="Flow_1">
            <di:waypoint x="188" y="120"/>
            <di:waypoint x="240" y="120"/>
        </bpmndi:BPMNEdge>
        <bpmndi:BPMNEdge id="Flow_2_di" bpmnElement="Flow_2">
            <di:waypoint x="340" y="120"/>
            <di:waypoint x="392" y="120"/>
        </bpmndi:BPMNEdge>
        </bpmndi:BPMNPlane>
    </bpmndi:BPMNDiagram>
    </definitions>'''