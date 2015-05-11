#!/usr/bin/env python

# Utility for Gcode transformation.
#
# Currently used to rewrite G02/G03 arcs using R/radius to using IJK/center point offset
# (see http://community.carbide3d.com/t/great-results-milling-pcbs-from-eagle-with-the-nomad/283/4)
#
# Maintained at https://github.com/liyanage/gcode-transform/
#

import sys
import os
import re
import argparse
import logging
import math


class Vector2D(object):

    def __init__(self, u, v):
        self.u = u
        self.v = v
    
    def angle_to_vector(self, other):
        a = math.atan2(self.v, self.u)

        # atan2 returns the angle from 0 to pi (y > 0) and 0 to -pi (y < 0),
        # we map that to 0 to 2*pi

        if a < 0:
            a += 2 * math.pi
            
        a2 = math.atan2(other.v, other.u)
        if a2 < 0:
            a2 += 2 * math.pi
        
        return a2 - a

    def __repr__(self):
        return '<Vector2D {} {}>'.format(self.u, self.v)


class Point2D(object):

    def __init__(self, x, y):
        self.x = x
        self.y = y
    
    def distance_to_point(self, other):
        x = self.x - other.x
        y = self.y - other.y
        return math.sqrt(x * x + y * y)
    
    def interpolate_to_point(self, other, ratio):
        assert ratio >= 0.0 and ratio <= 1.0
        return Point2D((1.0 - ratio) * self.x + ratio * other.x, (1.0 - ratio) * self.y + ratio * other.y)

    def vector_to_point(self, other):
        return Vector2D(other.x - self.x, other.y - self.y)

    def __repr__(self):
        return '<Point2D {} {}>'.format(self.x, self.y)


class Point3D(object):

    def __init__(self, x=0, y=0, z=0):
        self.x = x
        self.y = y
        self.z = z
    
    def point2d(self):
        return Point2D(self.x, self.y)
    
    def update_from_axes(self, axes):
        for axis, value in axes:
            value = float(value)
            if axis == 'X':
                self.x = value
            elif axis == 'Y':
                self.y = value
            elif axis == 'Z':
                self.z = value
    
    def __repr__(self):
        return '<Point3D {} {} {}>'.format(self.x, self.y, self.z)


class GcodeProcessor(object):

    def __init__(self):
        self.output_lines = []
        self.position = Point3D()
    
    def process_line(self, line):
        line = line.strip()
        self.push_line(line)
    
    def push_line(self, line):
        axis_re = re.compile(r'(X|Y|Z)([\d.-]+)')
        radius_re = re.compile(r'R([\d.-]+)')
        axes = []
        if re.match(r'\bG0*[01]\b\s*', line):
            axes = re.findall(r'(X|Y|Z)([\d.-]+)', line)
        elif re.match(r'\bG0*[23]\b\s*', line):
            axes = re.findall(r'(X|Y|Z)([\d.-]+)', line)
            radius_words = radius_re.findall(line)
            if radius_words:
                radius = float(radius_words[0])
                arc_end_point = Point3D()
                arc_end_point.update_from_axes(axes)
                arc_end_point = arc_end_point.point2d()
                self_2d = self.position.point2d()
                
                distance = self_2d.distance_to_point(arc_end_point)
                if distance > 2 * radius:
                    raise Exception('No intersection')

                # Find intersection points for two circles: http://stackoverflow.com/a/3349134/182781
                h = math.sqrt(radius * radius - distance/2 * distance/2)
                midpoint = self_2d.interpolate_to_point(arc_end_point, 0.5)
                x3_1 = midpoint.x + h * (arc_end_point.y - self.position.y) / distance
                x3_2 = midpoint.x + (-h) * (arc_end_point.y - self.position.y) / distance
                y3_1 = midpoint.y + (-h) * (arc_end_point.x - self.position.x) / distance
                y3_2 = midpoint.y + h * (arc_end_point.x - self.position.x) / distance
                p3_1 = Point2D(x3_1, y3_1)
                p3_2 = Point2D(x3_2, y3_2)
                
                # Calculate the angles between the vector from arc start to end point
                # and the two vectors from the arc start point to the two circle centerpoints
                v = self_2d.vector_to_point(arc_end_point)
                v1 = self_2d.vector_to_point(p3_1)
                v2 = self_2d.vector_to_point(p3_2)

                a1 = v.angle_to_vector(v1)
                if a1 > math.pi:
                    a1 -= 2 * math.pi
                a2 = v.angle_to_vector(v2)
                if a2 > math.pi:
                    a2 -= 2 * math.pi

                # Depending on the direction of the arc, pick the circle centerpoint
                # whose angle is further in that direction
                center = None                    
                command = re.findall(r'\bG0*([23])\b', line)
                if command[0] == '3':
                    # counterclockwise
                    center = p3_2 if a2 > a1 else p3_1
                else:
                    # clockwise
                    center = p3_2 if a2 < a1 else p3_1
                
                offset_vector = self_2d.vector_to_point(center)
                offset_word = 'I{}J{}'.format(round(offset_vector.u, 4) + 0.0, round(offset_vector.v, 4) + 0.0)
                
                line = re.sub(radius_re, offset_word, line)
        
        if axes:
            self.position.update_from_axes(axes)

        self.output_lines.append(line)
    
    def output(self):
        return ''.join([line + '\n' for line in self.output_lines])


class Tool(object):

    def __init__(self, args):
        self.args = args

    def run(self):
        with open(os.path.expanduser(self.args.gcode_path)) as f:
            self.process_file(f)
        
    def process_file(self, f):
        processor = GcodeProcessor()
        for line in f:
            processor.process_line(line)
        
        print processor.output()

    @classmethod
    def main(cls):
        parser = argparse.ArgumentParser(description='Munge gcode')
        parser.add_argument('gcode_path', help='Path to gcode file')
        parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose debug logging')

        args = parser.parse_args()
        if args.verbose:
            logging.basicConfig(level=logging.DEBUG)

        cls(args).run()


if __name__ == "__main__":
    Tool.main()
