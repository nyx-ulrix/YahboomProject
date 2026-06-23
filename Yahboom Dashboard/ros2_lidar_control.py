# We area not using this anymore 
import math
import socket
import threading
import json
import os

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from sensor_msgs.msg import LaserScan


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "lidar.json")

    with open(config_path, "r") as f:
        return json.load(f)


class Ros2UdpLidarController(Node):
    def __init__(self):
        super().__init__("ros2_udp_lidar_controller")

        self.cfg = load_config()

        self.cmd_vel_pub = self.create_publisher(
            Twist,
            self.cfg["cmd_vel_topic"],
            10
        )

        self.scan_sub = self.create_subscription(
            LaserScan,
            self.cfg["scan_topic"],
            self.scan_callback,
            10
        )

        self.current_manual_command = "STOP"
        self.auto_avoidance_active = False
        self.emergency_stop_active = False

        self.front_distance = float("inf")
        self.left_distance = float("inf")
        self.right_distance = float("inf")

        threading.Thread(target=self.udp_receiver_loop, daemon=True).start()

        self.create_timer(
            self.cfg["control_period"],
            self.control_loop
        )

        self.get_logger().info(
            "ROS2 UDP LiDAR Controller started. YOLO is perception-only."
        )

    def valid_min(self, vals):
        valid = [
            v for v in vals
            if v is not None and not math.isnan(v)
            and not math.isinf(v) and v > 0.0
        ]
        return min(valid) if valid else float("inf")

    def scan_callback(self, msg):
        front, left, right = [], [], []
        angle = msg.angle_min

        for d in msg.ranges:
            deg = math.degrees(angle)

            if -25 <= deg <= 25:
                front.append(d)
            elif 45 <= deg <= 135:
                left.append(d)
            elif -135 <= deg <= -45:
                right.append(d)

            angle += msg.angle_increment

        self.front_distance = self.valid_min(front)
        self.left_distance = self.valid_min(left)
        self.right_distance = self.valid_min(right)

    def udp_receiver_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.cfg["udp_ip"], self.cfg["udp_port"]))

        while True:
            data, _ = sock.recvfrom(1024)
            cmd = data.decode(errors="ignore").strip().upper()

            if cmd:
                self.handle_udp_command(cmd)

    def handle_udp_command(self, cmd):
        self.get_logger().info(f"UDP command received: {cmd}")

        if cmd == "EMERGENCY_STOP":
            self.emergency_stop_active = True
            self.auto_avoidance_active = False
            self.current_manual_command = "STOP"
            self.stop_robot()
            return

        if cmd == "RESET_STOP":
            self.emergency_stop_active = False
            self.current_manual_command = "STOP"
            self.stop_robot()
            return

        if cmd == "AUTO_AVOIDANCE":
            if not self.emergency_stop_active:
                self.auto_avoidance_active = True
                self.current_manual_command = "STOP"
            return

        if cmd in ["STOP_AUTO", "MANUAL"]:
            self.auto_avoidance_active = False
            self.current_manual_command = "STOP"
            self.stop_robot()
            return

        if cmd in ["YOLO_STOP", "YOLO_CLEAR", "START_YOLO", "STOP_YOLO"]:
            self.get_logger().info(
                f"{cmd} ignored for motion. YOLO is perception-only."
            )
            return

        if cmd in ["FORWARD", "BACKWARD", "LEFT", "RIGHT", "STOP"]:
            self.auto_avoidance_active = False
            self.current_manual_command = cmd

            if cmd == "STOP":
                self.stop_robot()

            return

    def publish_velocity(self, lx, az):
        twist = Twist()
        twist.linear.x = float(lx)
        twist.angular.z = float(az)
        self.cmd_vel_pub.publish(twist)

    def stop_robot(self):
        self.publish_velocity(0.0, 0.0)

    def manual_control(self, cmd):
        fb = self.front_distance < self.cfg["safe_front_distance"]
        lb = self.left_distance < self.cfg["safe_side_distance"]
        rb = self.right_distance < self.cfg["safe_side_distance"]

        if cmd == "STOP":
            self.stop_robot()

        elif cmd == "FORWARD":
            if not fb:
                self.publish_velocity(self.cfg["forward_speed"], 0.0)

            elif not lb and self.left_distance >= self.right_distance:
                self.publish_velocity(0.0, self.cfg["turn_speed"])

            elif not rb:
                self.publish_velocity(0.0, -self.cfg["turn_speed"])

            else:
                self.stop_robot()

        elif cmd == "LEFT":
            if not lb:
                self.publish_velocity(0.0, self.cfg["turn_speed"])
            else:
                self.stop_robot()

        elif cmd == "RIGHT":
            if not rb:
                self.publish_velocity(0.0, -self.cfg["turn_speed"])
            else:
                self.stop_robot()

        elif cmd == "BACKWARD":
            self.publish_velocity(self.cfg["backward_speed"], 0.0)

    def auto_control(self):
        fb = self.front_distance < self.cfg["auto_front_distance"]
        lb = self.left_distance < self.cfg["auto_side_distance"]
        rb = self.right_distance < self.cfg["auto_side_distance"]

        if not fb:
            self.publish_velocity(self.cfg["auto_forward_speed"], 0.0)

        elif not lb and self.left_distance >= self.right_distance:
            self.publish_velocity(0.0, self.cfg["auto_turn_speed"])

        elif not rb:
            self.publish_velocity(0.0, -self.cfg["auto_turn_speed"])

        else:
            self.stop_robot()

    def control_loop(self):
        if self.emergency_stop_active:
            self.stop_robot()

        elif self.auto_avoidance_active:
            self.auto_control()

        else:
            self.manual_control(self.current_manual_command)


def main(args=None):
    rclpy.init(args=args)
    node = Ros2UdpLidarController()

    try:
        rclpy.spin(node)

    except KeyboardInterrupt:
        pass

    finally:
        node.stop_robot()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
