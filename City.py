from RoadTypes import Segment
from Constants import *
from Utils import *
from Point import *
import noise
import math
from pyqtree import Index
import logging
from osmtype import *

class City(object):
    def __init__(self):
        super(City, self).__init__()
        self.heatmap = HeatMap()
        self.segments = list()
        
        self.nodes = list()
        self.node_id = 0
        self.ways = dict()
        self.road_id = 0

        self.road_index = Index(bbox=(
            QUADTREE_PARAMS_X,
            QUADTREE_PARAMS_Y,
            QUADTREE_PARAMS_X + QUADTREE_PARAMS_W,
            QUADTREE_PARAMS_Y + QUADTREE_PARAMS_H
        ))

        self.node_index = Index(bbox=(
            QUADTREE_PARAMS_X,
            QUADTREE_PARAMS_Y,
            QUADTREE_PARAMS_X + QUADTREE_PARAMS_W,
            QUADTREE_PARAMS_Y + QUADTREE_PARAMS_H
        ))

    def add_node(self, p):
        # search for dup
        near_node_ids = self.node_index.intersect((p.x, p.y, p.x, p.y))

        if len(near_node_ids) > 0: # dup exists
            # if the new node A is quite close to an existing node B, use B
            return near_node_ids[0]
        else:
            # add a new node
            self.nodes.append(Node(self.node_id, p.x, p.y, 0))
            self.node_index.insert(
                self.node_id, (p.x-NODE_SNAP_DISTANCE, p.y-NODE_SNAP_DISTANCE, p.x+NODE_SNAP_DISTANCE, p.y+NODE_SNAP_DISTANCE))
            self.node_id = self.node_id + 1
            return self.node_id - 1


    def append_segment(self, segment):
        segment.meta['start_id'] = self.add_node(segment.road.start)
        segment.meta['end_id'] = self.add_node(segment.road.end)
        

        if segment.meta['new_segment']:
            segment.meta['id'] = self.road_id
            self.road_id += 1
            segment.meta['nodes'] = list()
            segment.meta['nodes'].append(segment.meta['start_id'])
            segment.meta['nodes'].append(segment.meta['end_id'])

            self.ways[segment.meta['id']] = Way(segment.meta['id'], segment.meta['nodes'], segment.meta['highway'], segment.meta['length'])
        else:
            segment.meta['nodes'].append(segment.meta['end_id'])

            self.ways[segment.meta['id']].nodes_id.append(segment.meta['end_id'])
            self.ways[segment.meta['id']].length += segment.meta['length']


        # self.nodes[segment.meta['start_id']].r.append(segment.meta['id'])
        # self.nodes[segment.meta['end_id']].r.append(segment.meta['id'])

        self.segments.append(segment)
        self.road_index.insert(segment, (segment.getBox()))
        logging.info('append road, meta: ' +str(segment.meta) + ' road: ' + segment.road.to_string())


    # operating only meta here
    def gen_segment(self, start, delay, meta, dir, new_segment = True): 
        meta = meta.copy()
        
        length = 0
        if meta['highway']:
            length = HIGHWAY_SEGMENT_LENGTH + rand_in_limit(HIGHWAY_SEGMENT_LENGTH_OFFSET_LIMIT)
        else:
            length = STREET_SEGMENT_LENGTH + rand_in_limit(STREET_SEGMENT_LENGTH_OFFSET_LIMIT)

        end = Point(
            start.x + length*math.sin(math.radians(dir)),
            start.y + length*math.cos(math.radians(dir))
        )

        near_node_ids = self.node_index.intersect((end.x, end.y, end.x, end.y))
        if len(near_node_ids) > 0: # dup exists
            # if the new node A is quite close to an existing node B, use B
            end = self.nodes[near_node_ids[0]]

        if new_segment:
            meta['snapped'] = False
            if meta['highway']:
                meta['width'] = HIGHWAY_SEGMENT_WIDTH
            else:
                meta['width'] = STREET_SEGMENT_WIDTH + \
                    rand_in_limit(STREET_SEGMENT_WIDTH_OFFSET_LIMIT)
            meta['length'] = length
            meta['new_segment'] = True

        else:
            meta['length'] += length
            meta['new_segment'] = False

        res = Segment(start, end, delay, meta)
        logging.debug('gen new road, meta: ' +str(meta) + ' road: ' + res.road.to_string())

        return res

    def gen_segment_follow(self, previous_segment, dir):
        return self.gen_segment(
            previous_segment.road.end,
            STRENCH_TIME_DELAY_HIGHWAY if previous_segment.meta['highway'] else STRENCH_TIME_DELAY_STREET,
            previous_segment.meta,
            dir,
            new_segment = False
        )

    def gen_segment_branch(self, previous_segment, dir):
        new_meta = previous_segment.meta.copy()
        if previous_segment.meta['highway']:
            if rand_hit_thershold(HIGHWAY_DEGENERATE_PROBABILITY):
                new_meta['highway'] = False
                return self.gen_segment(
                    previous_segment.road.end,
                    BRANCH_TIME_DELAY_HIGHWAY,
                    new_meta,
                    dir,
                    new_segment = True
                )
            else:
                return self.gen_segment(
                    previous_segment.road.end,
                    BRANCH_TIME_DELAY_HIGHWAY,
                    new_meta,
                    dir,
                    new_segment = True
                )
        else:
            return self.gen_segment(
                previous_segment.road.end,
                BRANCH_TIME_DELAY_STREET,
                new_meta,
                dir,
                new_segment = True
            )

    def globalGoals(self, previous_segment):
        proposed_segments = list()

        logging.info("previous: " + previous_segment.road.to_string())

        if 'snapped' not in previous_segment.meta or not previous_segment.meta['snapped']:
            straight_follow_segment = self.gen_segment_follow(
                previous_segment, previous_segment.dir() + rand_in_limit(STREET_CURVE_DIRECTION_OFFSET_LIMIT))
            straight_heat = self.heatmap.road_heat(
                straight_follow_segment.road)

            max_heat = None
            max_heat_offset = 0
            for offset in range(-HIGHWAY_CURVE_DIRECTION_OFFSET_LIMIT, HIGHWAY_CURVE_DIRECTION_OFFSET_LIMIT):
                curve_follow_segment = self.gen_segment_follow(
                    previous_segment, previous_segment.dir() + offset)
                heat = self.heatmap.road_heat(curve_follow_segment.road)
                if max_heat is None or heat > max_heat:
                    max_heat = heat
                    max_heat_offset = offset
            curve_follow_segment = self.gen_segment_follow(
                previous_segment, previous_segment.dir() + max_heat_offset)

            if previous_segment.meta['highway']:
                logging.info("is highway")
                proposed_segments.append(curve_follow_segment)
                logging.info("---gen [highway] curve follow: " + curve_follow_segment.road.to_string())

                if max_heat > HIGHWAY_BRANCH_HEAT_THRESHOLD:
                    if rand_hit_thershold(HIGHWAY_BRANCH_RIGHT_PROBABILITY):
                        leftHighwayBranch = self.gen_segment_branch(
                            previous_segment, previous_segment.dir() - 90 + rand_in_limit(HIGHWAY_BRANCH_DIRECTION_OFFSET_LIMIT))
                        proposed_segments.append(leftHighwayBranch)
                        logging.info("---gen [highway] left branch: " + leftHighwayBranch.road.to_string())

                    if rand_hit_thershold(HIGHWAY_BRANCH_RIGHT_PROBABILITY):
                        rightHighwayBranch = self.gen_segment_branch(
                            previous_segment, previous_segment.dir() + 90 + rand_in_limit(HIGHWAY_BRANCH_DIRECTION_OFFSET_LIMIT))
                        proposed_segments.append(rightHighwayBranch)
                        logging.info("---gen [highway] right branch: " + rightHighwayBranch.road.to_string())

            else:
                if rand_hit_thershold(max_heat*3):
                    proposed_segments.append(curve_follow_segment)
                    logging.info("---gen [street] follow branch: " + curve_follow_segment.road.to_string())

            if rand_hit_thershold(straight_heat*3):
                if rand_hit_thershold(STREET_BRANCH_LEFT_PROBABILITY):
                    leftBranch = self.gen_segment_branch(previous_segment, previous_segment.dir(
                    ) - 90 + rand_in_limit(STREET_BRANCH_DIRECTION_OFFSET_LIMIT))
                    proposed_segments.append(leftBranch)
                    logging.info("---gen [highway/street] left branch: " + leftBranch.road.to_string())

                if rand_hit_thershold(STREET_BRANCH_RIGHT_PROBABILITY):
                    rightBranch = self.gen_segment_branch(previous_segment, previous_segment.dir(
                    ) + 90 + rand_in_limit(STREET_BRANCH_DIRECTION_OFFSET_LIMIT))
                    proposed_segments.append(rightBranch)
                    logging.info("---gen [highway/street] left branch: " + rightBranch.road.to_string())

        return proposed_segments

    def localConstraints(self, segment, segments):
        # return True
        minx, miny, maxx, maxy = segment.getBox()
        matchSegments = self.road_index.intersect(
            (minx - ROAD_SNAP_DISTANCE,
             miny - ROAD_SNAP_DISTANCE,
             maxx + ROAD_SNAP_DISTANCE,
             maxy + ROAD_SNAP_DISTANCE)
        )

        for other in matchSegments:
            degree = min_intersect_degree(other.dir(), segment.dir())

            # 1. intersection check
            cross = line_cross([segment.road.start, segment.road.end], [
                               other.road.start, other.road.end])
            if cross != False:
                if not cross.equal(segment.road.start) and not cross.equal(segment.road.end):
                    # cross other line with small angle
                    if degree < MINIMUM_INTERSECTION_DEVIATION:
                        return False

                    segment.road.end = cross
                    segment.meta['snapped'] = True

            else:
                # 2. snap to crossing within radius check
                if distance_p2p(segment.road.end, other.road.end) <= ROAD_SNAP_DISTANCE:
                    segment.road.end = other.road.end
                    segment.meta['snapped'] = True

                # 3. intersection within radius check
                distance = distance_p2l(segment.road.end, other.road)
                if distance <= ROAD_SNAP_DISTANCE and distance > EPSILON:
                    if degree >= MINIMUM_INTERSECTION_DEVIATION:
                        project_point = point_projection(
                            segment.road.end, other.e.start, other.road.end)
                        segment.road.end = project_point
                        segment.meta['snapped'] = True
        return True

    def generate(self):
        priority_queue = list()

        priority_queue.append(self.gen_segment(Point(0, 0), 0, {'highway': True}, 0, HIGHWAY_SEGMENT_LENGTH))
        priority_queue.append(self.gen_segment(Point(0, 0), 0, {'highway': True}, 90, HIGHWAY_SEGMENT_LENGTH))
        priority_queue.append(self.gen_segment(Point(0, 0), 0, {'highway': True}, 180, HIGHWAY_SEGMENT_LENGTH))
        priority_queue.append(self.gen_segment(Point(0, 0), 0, {'highway': True}, -90, HIGHWAY_SEGMENT_LENGTH))

        while len(priority_queue) > 0 and len(self.segments) < SEGMENT_COUNT_LIMIT:
            if len(self.segments) % 100 == 0:
                print(len(self.segments))
            # pop smallest road(ti, ri, qi) from Q
            min_t = None
            min_index = 0
            for i, segment in enumerate(priority_queue):
                if min_t is None or segment.delay < min_t:
                    min_t = segment.delay
                    min_index = i

            min_segment = priority_queue.pop(min_index)
            accepted = self.localConstraints(min_segment, self.segments)
            if accepted:
                self.append_segment(min_segment)
                newSegments = self.globalGoals(min_segment)
                for i, newSegment in enumerate(newSegments):
                    newSegments[i].delay = min_segment.delay + 1 + newSegments[i].delay
                    priority_queue.append(newSegment)

            else:
                logging.info('segment is rejected!, which is :')
                logging.info('   ' + min_segment.road.to_string())
                logging.info('   is highway: ' + str(min_segment.meta['highway']))
        self.post_process()


    def post_process(self):
        max_length = 0
        for segment in self.segments:
            if not segment.meta['highway']:
                if segment.meta['length'] > max_length:
                    max_length = segment.meta['length']


        for way_key, way_value in self.ways.items():
            if not way_value.is_highway:
                if way_value.length * 1.0/ max_length < 0.33:
                    self.ways[way_key].width = 1

                elif way_value.length * 1.0/max_length < 0.66:
                    self.ways[way_key].width = 2

                else:
                    self.ways[way_key].width = 3
            else:
                self.ways[way_key].width = HIGHWAY_SEGMENT_WIDTH
        

class HeatMap(object):
    def road_heat(self, road):
        return (self.population(road.start.x, road.start.y) + self.population(road.end.x, road.end.y))/2

    def population(self, x, y):
        value1 = (noise.snoise2(x/10000, y/10000) + 1) / 2
        value2 = (noise.snoise2(x/20000 + 500, y/20000 + 500) + 1) / 2
        value3 = (noise.snoise2(x/20000 + 1000, y/20000 + 1000) + 1) / 2
        return pow((value1 * value2 + value3) / 2, 2)


