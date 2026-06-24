import rclpy
import json
import time
import numpy as np
import paho.mqtt.client as mqtt

from rclpy.node import Node
from geometry_msgs.msg import Twist
from std_msgs.msg import Int32
from sensor_msgs.msg import LaserScan


# =========================
# MQTT SETTINGS
# =========================
BROKER_IP = "localhost"

TOPIC = "yahboom/cmd"
TOPIC_GRID = "yahboom/grid"
TOPIC_DRIVE_STATUS = "yahboom/drive/status"


# =========================
# MANUAL MOVEMENT SETTINGS
# =========================
LINEAR_SPEED = 0.5
ANGULAR_SPEED = 1.0

PUBLISH_RATE = 20.0
LINEAR_STEP = 0.02
ANGULAR_STEP = 0.05


# =========================
# AUTO MOVEMENT SETTINGS
# =========================
AUTO_LINEAR_SPEED = 0.25
AUTO_MIN_FORWARD_SPEED = 0.08
AUTO_TURN_SPEED = 0.65

AUTO_BLOCK_DISTANCE = 0.40
AUTO_FORWARD_CLEARANCE = 0.75
AUTO_FORWARD_SLOWDOWN = 1.0

TURN_IN_PLACE_ANGLE = 18.0
FORWARD_STEER_ANGLE = 8.0

BEST_DIRECTION_WINDOW = 12.0
BEST_DIRECTION_MIN_ANGLE = -100.0
BEST_DIRECTION_MAX_ANGLE = 100.0
BEST_DIRECTION_STEP_DEG = 4.0

ROBOT_WIDTH_M = 0.15
GAP_SAFETY_MARGIN = 1.3
MIN_GAP_WIDTH_M = ROBOT_WIDTH_M * GAP_SAFETY_MARGIN

REAR_ANGLE = 25.0
REAR_LEFT_MIN = 110.0
REAR_LEFT_MAX = 160.0
REAR_RIGHT_MIN = -160.0
REAR_RIGHT_MAX = -110.0

AUTO_REVERSE_SPEED = 0.12
AUTO_REVERSE_CLEARANCE = 0.30
AUTO_REVERSE_TURN_GAIN = 0.5


# =========================
# LIDAR SETTINGS
# =========================
RAD2DEG = 180.0 / np.pi
MAX_SCAN_RANGE = 3.0

FRONT_ANGLE = 25.0
SIDE_ANGLE_MIN = 30.0
SIDE_ANGLE_MAX = 110.0

FRONT_LEFT_MIN = 20.0
FRONT_LEFT_MAX = 75.0
FRONT_RIGHT_MIN = -75.0
FRONT_RIGHT_MAX = -20.0

SECTOR_PERCENTILE = 25.0
MIN_POINTS_IN_SECTOR = 3


# =========================
# OCCUPANCY GRID SETTINGS
# =========================
GRID_RESOLUTION = 0.05
GRID_PUBLISH_RATE = 5.0

CELL_FREE = 0
CELL_OCCUPIED = 1
CELL_UNKNOWN = -1


# =========================
# CAMERA SERVO SETTINGS
# =========================
S1_MIN, S1_MAX = -90, 90
S2_MIN, S2_MAX = -90, 20

CAM_STEP = 10
S_CAM_STEP = 5


# =========================
# COMMAND GROUPS
# =========================
MOVEMENT_COMMANDS = {
    "fwd",
    "fwdright",
    "fwdleft",
    "bck",
    "bckright",
    "bckleft",
    "left",
    "right",
    "stop",
}

CAMERA_COMMANDS = {
    "cleft",
    "cright",
    "up",
    "upcleft",
    "upcright",
    "down",
    "downcleft",
    "downcright",
    "cstop",
    "crst",
}

ESTOP_COMMANDS = {
    "estop_on",
    "estop_off",
}

AUTO_COMMANDS = {
    "auto_on",
    "auto_off",
}


class MqttRosNode(Node):
    def __init__(self):
        super().__init__("mqtt_ros_cmd_vel_node")

        # =========================
        # ROS PUBLISHERS
        # =========================
        self.cmd_vel_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self.servo_s1_pub = self.create_publisher(Int32, "/servo_s1", 10)
        self.servo_s2_pub = self.create_publisher(Int32, "/servo_s2", 10)

        # =========================
        # ROS SUBSCRIBER: LIDAR
        # =========================
        self.scan_sub = self.create_subscription(
            LaserScan,
            "/scan",
            self.scan_callback,
            10
        )

        # =========================
        # MOVEMENT STATE
        # =========================
        self.target_linear_x = 0.0
        self.target_angular_z = 0.0

        self.current_linear_x = 0.0
        self.current_angular_z = 0.0

        self.last_command = "stop"

        # =========================
        # MODE / SAFETY STATE
        # =========================
        self.estop_active = False
        self.auto_mode = False

        # =========================
        # LIDAR STATE
        # =========================
        self.front_distance = None
        self.left_distance = None
        self.right_distance = None
        self.front_left_distance = None
        self.front_right_distance = None
        self.rear_distance = None
        self.rear_left_distance = None
        self.rear_right_distance = None

        self.scan_angles = np.array([], dtype=np.float32)
        self.scan_ranges = np.array([], dtype=np.float32)

        grid_cells = int((MAX_SCAN_RANGE * 2) / GRID_RESOLUTION)
        self.grid_size = grid_cells
        self.grid_centre = grid_cells // 2

        # =========================
        # SERVO STATE
        # =========================
        self.servo_s1 = 0
        self.servo_s2 = -60
        self.active_servo_cmd = None

        # =========================
        # AUTO LIDAR STATE
        # =========================
        self.auto_state = "idle" # idle | forward | turning_90

        # =========================
        # TIMERS
        # =========================
        self.movement_timer = self.create_timer(
            1.0 / PUBLISH_RATE,
            self.publish_cmd_vel
        )

        self.servo_timer = self.create_timer(
            0.1,
            self.servo_tick
        )

        self.grid_timer = self.create_timer(
            1.0 / GRID_PUBLISH_RATE,
            self.publish_grid_mqtt
        )

        # =========================
        # MQTT SETUP
        # =========================
        try:
            self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        except AttributeError:
            self.mqtt_client = mqtt.Client()

        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message

        self.get_logger().info(f"Connecting to MQTT broker at {BROKER_IP}:1883")
        self.mqtt_client.connect(BROKER_IP, 1883, 60)
        self.mqtt_client.loop_start()

        self.get_logger().info("MQTT ROS node started")
        self.get_logger().info(f"Listening to MQTT topic: {TOPIC}")
        self.get_logger().info(f"Publishing grid to MQTT topic: {TOPIC_GRID}")
        self.get_logger().info(f"Publishing drive status to MQTT topic: {TOPIC_DRIVE_STATUS}")
        self.get_logger().info(f"Grid: {self.grid_size}x{self.grid_size} cells @ {GRID_RESOLUTION}m/cell")
        self.get_logger().info("Publishing to ROS topics: /cmd_vel, /servo_s1, /servo_s2")
        self.get_logger().info("Hard e-stop: estop_on / estop_off")
        self.get_logger().info("Auto soft stop: auto_soft_stop")

    # =========================
    # MQTT CALLBACKS
    # =========================
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.get_logger().info("Connected to MQTT broker")
            client.subscribe(TOPIC)
            self.get_logger().info(f"Subscribed to MQTT topic: {TOPIC}")
        else:
            self.get_logger().error(f"Failed to connect to MQTT broker. Code: {rc}")

    def on_message(self, client, userdata, msg):
        command = msg.payload.decode().strip().lower()

        if command != self.last_command:
            self.get_logger().info(f"Received MQTT command: {command}")
            self.last_command = command

        # =========================
        # MODE COMMANDS
        # =========================
        if command == "auto_on":
            self.auto_mode = True
            self.auto_state = "active"
            self.force_zero_motion()
            self.publish_drive_status("auto_enabled")
            self.get_logger().info("AUTO MODE ENABLED")
            return

        elif command == "auto_off":
            self.auto_mode = False
            self.auto_state = "idle"
            self.force_zero_motion()
            self.stop_robot_now()
            self.publish_drive_status("auto_disabled")
            self.get_logger().info("AUTO MODE DISABLED")
            return

        # =========================
        # HARD E-STOP COMMANDS
        # =========================
        if command == "estop_on":
            self.enable_estop()
            return

        elif command == "estop_off":
            self.disable_estop()
            return

        # =========================
        # AUTO SOFT STOP
        # This stops the robot but does NOT latch e-stop.
        # Used when LiDAR detects obstacle during auto mode.
        # Client can still send left/right/fwd after this.
        # =========================
        if command == "auto_soft_stop":
            self.force_zero_motion()
            self.stop_robot_now()
            self.publish_drive_status("auto_soft_stop")
            self.get_logger().warn("AUTO SOFT STOP - robot halted, e-stop not latched")
            return

        # =========================
        # BLOCK MOVEMENT WHILE HARD E-STOP ACTIVE
        # =========================
        if self.estop_active and command in MOVEMENT_COMMANDS:
            self.force_zero_motion()
            self.stop_robot_now()
            self.publish_drive_status(f"blocked_by_estop:{command}")
            self.get_logger().warn(f"Command blocked because E-stop is active: {command}")
            return

        # =========================
        # MANUAL / CLIENT MOVEMENT COMMANDS
        # These commands work in both manual mode and auto/client-decision mode.
        # The client decides the direction during auto mode.
        # =========================
        if command == "fwd":
            self.auto_mode = False
            self.auto_state = "idle"
            self.target_linear_x = LINEAR_SPEED
            self.target_angular_z = 0.0
            self.publish_drive_status("moving_forward")

        elif command == "fwdright":
            self.auto_mode = False
            self.auto_state = "idle"
            self.target_linear_x = LINEAR_SPEED
            self.target_angular_z = -ANGULAR_SPEED
            self.publish_drive_status("moving_forward_right")

        elif command == "fwdleft":
            self.auto_mode = False
            self.auto_state = "idle"
            self.target_linear_x = LINEAR_SPEED
            self.target_angular_z = ANGULAR_SPEED
            self.publish_drive_status("moving_forward_left")

        elif command == "bck":
            self.auto_mode = False
            self.auto_state = "idle"
            self.target_linear_x = -LINEAR_SPEED
            self.target_angular_z = 0.0
            self.publish_drive_status("moving_backward")

        elif command == "bckright":
            self.auto_mode = False
            self.auto_state = "idle"
            self.target_linear_x = -LINEAR_SPEED
            self.target_angular_z = ANGULAR_SPEED
            self.publish_drive_status("moving_backward_right")

        elif command == "bckleft":
            self.auto_mode = False
            self.auto_state = "idle"
            self.target_linear_x = -LINEAR_SPEED
            self.target_angular_z = -ANGULAR_SPEED
            self.publish_drive_status("moving_backward_left")

        elif command == "left":
            self.auto_mode = False
            self.auto_state = "idle"
            self.target_linear_x = 0.0
            self.target_angular_z = ANGULAR_SPEED
            self.publish_drive_status("turning_left")

        elif command == "right":
            self.auto_mode = False
            self.auto_state = "idle"
            self.target_linear_x = 0.0
            self.target_angular_z = -ANGULAR_SPEED
            self.publish_drive_status("turning_right")

        elif command == "stop":
            self.auto_mode = False
            self.auto_state = "idle"
            self.force_zero_motion()
            self.stop_robot_now()
            self.publish_drive_status("stopped")

        # =========================
        # CAMERA MOVEMENT COMMANDS
        # =========================
        elif command == "cleft":
            self.active_servo_cmd = "cleft"

        elif command == "cright":
            self.active_servo_cmd = "cright"

        elif command == "up":
            self.active_servo_cmd = "up"

        elif command == "upcleft":
            self.active_servo_cmd = "upcleft"

        elif command == "upcright":
            self.active_servo_cmd = "upcright"

        elif command == "down":
            self.active_servo_cmd = "down"

        elif command == "downcleft":
            self.active_servo_cmd = "downcleft"

        elif command == "downcright":
            self.active_servo_cmd = "downcright"

        elif command == "cstop":
            self.active_servo_cmd = None

        elif command == "crst":
            self.active_servo_cmd = None
            self.servo_s1 = 0
            self.servo_s2 = -60
            self.publish_servos()

        else:
            self.get_logger().warn(f"Unknown command: {command}")
            self.publish_drive_status(f"unknown_command:{command}")

    def wrap_angle_deg(self, angle_deg):
        return ((angle_deg + 180.0) % 360.0) - 180.0

    def sector_percentile_distance(self, min_angle, max_angle, percentile=SECTOR_PERCENTILE):
        if len(self.scan_angles) == 0:
            return None

        mask = (self.scan_angles >= min_angle) & (self.scan_angles <= max_angle)
        vals = self.scan_ranges[mask]

        if len(vals) < MIN_POINTS_IN_SECTOR:
            return None

        return float(np.percentile(vals, percentile))

    # =========================
    # LIDAR CALLBACK
    # =========================
    def scan_callback(self, scan_data):
        ranges = np.array(scan_data.ranges, dtype=np.float32)

        angles = (
            scan_data.angle_min
            + scan_data.angle_increment * np.arange(len(ranges))
        ) * RAD2DEG

        angles = np.array([self.wrap_angle_deg(a) for a in angles], dtype=np.float32)

        valid_mask = (
            np.isfinite(ranges)
            & (ranges >= scan_data.range_min)
            & (ranges <= min(scan_data.range_max, MAX_SCAN_RANGE))
        )

        self.scan_angles = angles[valid_mask]
        self.scan_ranges = ranges[valid_mask]

        self.front_distance = self.sector_percentile_distance(-FRONT_ANGLE, FRONT_ANGLE, 20.0)
        self.left_distance = self.sector_percentile_distance(SIDE_ANGLE_MIN, SIDE_ANGLE_MAX, 25.0)
        self.right_distance = self.sector_percentile_distance(-SIDE_ANGLE_MAX, -SIDE_ANGLE_MIN, 25.0)
        self.front_left_distance = self.sector_percentile_distance(FRONT_LEFT_MIN, FRONT_LEFT_MAX, 25.0)
        self.front_right_distance = self.sector_percentile_distance(FRONT_RIGHT_MIN, FRONT_RIGHT_MAX, 25.0)

        rear_a = self.sector_percentile_distance(180.0 - REAR_ANGLE, 180.0, 25.0)
        rear_b = self.sector_percentile_distance(-180.0, -180.0 + REAR_ANGLE, 25.0)
        rear_candidates = [v for v in (rear_a, rear_b) if v is not None]
        self.rear_distance = min(rear_candidates) if rear_candidates else None

        self.rear_left_distance = self.sector_percentile_distance(REAR_LEFT_MIN, REAR_LEFT_MAX, 25.0)
        self.rear_right_distance = self.sector_percentile_distance(REAR_RIGHT_MIN, REAR_RIGHT_MAX, 25.0)

    # =========================
    # OCCUPANCY GRID BUILDER
    # =========================
    def build_grid(self):
        size = self.grid_size
        centre = self.grid_centre

        grid = np.full((size, size), CELL_UNKNOWN, dtype=np.int8)

        if len(self.scan_angles) == 0:
            return grid.tolist()

        angles_rad = np.deg2rad(self.scan_angles)

        for angle_r, dist in zip(angles_rad, self.scan_ranges):
            x_m = dist * np.cos(angle_r)
            y_m = dist * np.sin(angle_r)

            steps = max(1, int(dist / GRID_RESOLUTION))

            for step in range(steps):
                frac = step / steps
                xi = x_m * frac
                yi = y_m * frac

                row = centre - int(round(xi / GRID_RESOLUTION))
                col = centre + int(round(yi / GRID_RESOLUTION))

                if 0 <= row < size and 0 <= col < size:
                    if grid[row, col] != CELL_OCCUPIED:
                        grid[row, col] = CELL_FREE

            end_row = centre - int(round(x_m / GRID_RESOLUTION))
            end_col = centre + int(round(y_m / GRID_RESOLUTION))

            if 0 <= end_row < size and 0 <= end_col < size:
                grid[end_row, end_col] = CELL_OCCUPIED

        return grid.tolist()

    # =========================
    # PUBLISH GRID OVER MQTT
    # =========================
    def publish_grid_mqtt(self):
        if len(self.scan_ranges) == 0:
            return

        grid = self.build_grid()

        payload = {
            "grid": grid,
            "resolution": GRID_RESOLUTION,
            "size": self.grid_size,
            "robot_row": self.grid_centre,
            "robot_col": self.grid_centre,
            "front": self.front_distance,
            "left": self.left_distance,
            "right": self.right_distance,
            "front_left": self.front_left_distance,
            "front_right": self.front_right_distance,
            "rear": self.rear_distance,
            "rear_left": self.rear_left_distance,
            "rear_right": self.rear_right_distance,
            "auto_mode": self.auto_mode,
            "estop_active": self.estop_active,
            "timestamp": time.time(),
        }

        try:
            self.mqtt_client.publish(TOPIC_GRID, json.dumps(payload), qos=0)
        except Exception as e:
            self.get_logger().warn(f"Failed to publish grid: {e}")

    # =========================
    # DRIVE STATUS PUBLISHER
    # =========================
    def publish_drive_status(self, status):
        payload = {
            "status": status,
            "auto_mode": self.auto_mode,
            "estop_active": self.estop_active,
            "front": self.front_distance,
            "left": self.left_distance,
            "right": self.right_distance,
            "front_left": self.front_left_distance,
            "front_right": self.front_right_distance,
            "rear": self.rear_distance,
            "rear_left": self.rear_left_distance,
            "rear_right": self.rear_right_distance,
            "timestamp": time.time(),
        }

        try:
            self.mqtt_client.publish(TOPIC_DRIVE_STATUS, json.dumps(payload), qos=0)
        except Exception as e:
            self.get_logger().warn(f"Failed to publish drive status: {e}")

    # =========================
    # E-STOP HELPERS
    # =========================
    def enable_estop(self):
        self.estop_active = True
        self.auto_mode = False
        self.auto_state = "idle"
        self.active_servo_cmd = None
        self.force_zero_motion()
        self.stop_robot_now()
        self.publish_drive_status("estop_active")
        self.get_logger().warn("EMERGENCY STOP ACTIVE - robot halted and movement locked")

    def disable_estop(self):
        self.estop_active = False
        self.auto_mode = False
        self.auto_state = "idle"
        self.force_zero_motion()
        self.stop_robot_now()
        self.publish_drive_status("estop_cleared")
        self.get_logger().info("EMERGENCY STOP CLEARED - robot control enabled")

    def force_zero_motion(self):
        self.target_linear_x = 0.0
        self.target_angular_z = 0.0
        self.current_linear_x = 0.0
        self.current_angular_z = 0.0

    # =========================
    # SMOOTHING
    # =========================
    def ramp_value(self, current, target, step):
        if abs(target - current) <= step:
            return target
        return current + step if current < target else current - step

    def find_best_direction(self):
        if len(self.scan_angles) == 0:
            return None

        best_angle = None
        best_score = -1.0
        half_window = BEST_DIRECTION_WINDOW / 2.0

        for candidate in np.arange(
            BEST_DIRECTION_MIN_ANGLE,
            BEST_DIRECTION_MAX_ANGLE + BEST_DIRECTION_STEP_DEG,
            BEST_DIRECTION_STEP_DEG
        ):
            mask = (
                (self.scan_angles >= candidate - half_window)
                & (self.scan_angles <= candidate + half_window)
            )

            sector_ranges = self.scan_ranges[mask]
            if len(sector_ranges) < MIN_POINTS_IN_SECTOR:
                continue

            nearest = float(np.percentile(sector_ranges, 20.0))
            typical = float(np.percentile(sector_ranges, 60.0))

            half_window_rad = np.deg2rad(half_window)
            estimated_gap_width = 2.0 * nearest * np.tan(half_window_rad)

            if estimated_gap_width < MIN_GAP_WIDTH_M:
                continue

            forward_weight = max(0.2, 1.0 - (abs(candidate) / 120.0))
            score = (0.65 * typical + 0.35 * nearest) * forward_weight

            if score > best_score:
                best_score = score
                best_angle = float(candidate)

        return best_angle

    def format_distance_angle(self, angle):
        if angle is None:
            return "unknown"
        if abs(angle) < 1.0:
            return "straight"
        side = "left" if angle > 0.0 else "right"
        return f"{abs(angle):.1f} deg {side}"

    # =========================
    # AUTO MOVEMENT LOGIC
    # =========================
    def auto_movement_logic(self):
        if len(self.scan_angles) == 0 or self.front_distance is None:
            self.force_zero_motion()
            self.publish_drive_status("auto_waiting_for_scan")
            return

        front = self.front_distance if self.front_distance is not None else 0.0
        front_left = self.front_left_distance if self.front_left_distance is not None else 0.0
        front_right = self.front_right_distance if self.front_right_distance is not None else 0.0
        left = self.left_distance if self.left_distance is not None else 0.0
        right = self.right_distance if self.right_distance is not None else 0.0

        front_blocked = front < AUTO_BLOCK_DISTANCE
        best_angle = self.find_best_direction()

        if not front_blocked:
            steer_bias = 0.0
            if front_left > 0.0 and front_right > 0.0:
                steer_bias = np.clip((front_left - front_right) * 1.2, -0.35, 0.35)

            speed_scale = np.clip(
                (front - AUTO_BLOCK_DISTANCE) / max(0.01, (AUTO_FORWARD_CLEARANCE - AUTO_BLOCK_DISTANCE)),
                0.0,
                1.0
            )

            commanded_speed = AUTO_MIN_FORWARD_SPEED + \
                (AUTO_LINEAR_SPEED - AUTO_MIN_FORWARD_SPEED) * speed_scale * AUTO_FORWARD_SLOWDOWN

            self.target_linear_x = float(np.clip(
                commanded_speed,
                AUTO_MIN_FORWARD_SPEED,
                AUTO_LINEAR_SPEED
            ))
            self.target_angular_z = float(steer_bias)
            self.publish_drive_status("auto_forward_clear")
            return

        if best_angle is not None:
            if abs(best_angle) <= FORWARD_STEER_ANGLE:
                self.target_linear_x = AUTO_MIN_FORWARD_SPEED
                self.target_angular_z = 0.0
                self.publish_drive_status("auto_creep_forward")
                return

            if abs(best_angle) <= TURN_IN_PLACE_ANGLE:
                turn_sign = 1.0 if best_angle > 0.0 else -1.0
                self.target_linear_x = AUTO_MIN_FORWARD_SPEED
                self.target_angular_z = turn_sign * min(AUTO_TURN_SPEED * 0.6, 0.45)
                self.publish_drive_status("auto_steer_left" if turn_sign > 0 else "auto_steer_right")
                return

            turn_sign = 1.0 if best_angle > 0.0 else -1.0
            turn_scale = np.clip(abs(best_angle) / 90.0, 0.35, 1.0)

            self.target_linear_x = 0.0
            self.target_angular_z = float(turn_sign * AUTO_TURN_SPEED * turn_scale)

            if turn_sign > 0:
                self.publish_drive_status(f"auto_turn_left_gap_{best_angle:.0f}")
            else:
                self.publish_drive_status(f"auto_turn_right_gap_{abs(best_angle):.0f}")
            return

        if front_left > front_right and left >= right:
            self.target_linear_x = 0.0
            self.target_angular_z = AUTO_TURN_SPEED * 0.8
            self.publish_drive_status("auto_fallback_left")
            return

        if front_right >= front_left:
            self.target_linear_x = 0.0
            self.target_angular_z = -AUTO_TURN_SPEED * 0.8
            self.publish_drive_status("auto_fallback_right")
            return

        rear = self.rear_distance if self.rear_distance is not None else 0.0
        rear_left = self.rear_left_distance if self.rear_left_distance is not None else 0.0
        rear_right = self.rear_right_distance if self.rear_right_distance is not None else 0.0

        if rear >= AUTO_REVERSE_CLEARANCE:
            reverse_turn = 0.0
            if rear_left > 0.0 and rear_right > 0.0:
                reverse_turn = np.clip((rear_right - rear_left) * AUTO_REVERSE_TURN_GAIN, -0.35, 0.35)

            self.target_linear_x = -AUTO_REVERSE_SPEED
            self.target_angular_z = float(reverse_turn)
            self.publish_drive_status("auto_reverse_recovery")
            return

        self.force_zero_motion()
        self.stop_robot_now()
        self.publish_drive_status("auto_all_blocked_front_and_rear")

    # =========================
    # PUBLISH MOVEMENT
    # =========================
    def publish_cmd_vel(self):
        if self.estop_active:
            self.force_zero_motion()
            self.stop_robot_now()
            return

        if self.auto_mode:
            self.auto_movement_logic()

        self.current_linear_x = self.ramp_value(
            self.current_linear_x,
            self.target_linear_x,
            LINEAR_STEP
        )

        self.current_angular_z = self.ramp_value(
            self.current_angular_z,
            self.target_angular_z,
            ANGULAR_STEP
        )

        twist = Twist()
        twist.linear.x = self.current_linear_x
        twist.linear.y = 0.0
        twist.linear.z = 0.0
        twist.angular.x = 0.0
        twist.angular.y = 0.0
        twist.angular.z = self.current_angular_z

        self.cmd_vel_pub.publish(twist)

    # =========================
    # SERVO MOVEMENT
    # =========================
    def servo_tick(self):
        if self.active_servo_cmd is None:
            return

        cmd = self.active_servo_cmd
        changed = True

        if cmd == "cleft":
            self.servo_s1 = max(S1_MIN, self.servo_s1 - CAM_STEP)

        elif cmd == "cright":
            self.servo_s1 = min(S1_MAX, self.servo_s1 + CAM_STEP)

        elif cmd == "up":
            self.servo_s2 = min(S2_MAX, self.servo_s2 + CAM_STEP)

        elif cmd == "down":
            self.servo_s2 = max(S2_MIN, self.servo_s2 - CAM_STEP)

        elif cmd == "upcleft":
            self.servo_s1 = max(S1_MIN, self.servo_s1 - S_CAM_STEP)
            self.servo_s2 = min(S2_MAX, self.servo_s2 + S_CAM_STEP)

        elif cmd == "upcright":
            self.servo_s1 = min(S1_MAX, self.servo_s1 + S_CAM_STEP)
            self.servo_s2 = min(S2_MAX, self.servo_s2 + S_CAM_STEP)

        elif cmd == "downcleft":
            self.servo_s1 = max(S1_MIN, self.servo_s1 - S_CAM_STEP)
            self.servo_s2 = max(S2_MIN, self.servo_s2 - S_CAM_STEP)

        elif cmd == "downcright":
            self.servo_s1 = min(S1_MAX, self.servo_s1 + S_CAM_STEP)
            self.servo_s2 = max(S2_MIN, self.servo_s2 - S_CAM_STEP)

        else:
            changed = False

        s1_at_limit = self.servo_s1 <= S1_MIN or self.servo_s1 >= S1_MAX
        s2_at_limit = self.servo_s2 <= S2_MIN or self.servo_s2 >= S2_MAX

        if cmd in ("cleft", "cright") and s1_at_limit:
            self.active_servo_cmd = None
        elif cmd in ("up", "down") and s2_at_limit:
            self.active_servo_cmd = None

        if changed:
            self.publish_servos()

    def publish_servos(self):
        msg_s1 = Int32()
        msg_s1.data = self.servo_s1
        self.servo_s1_pub.publish(msg_s1)

        msg_s2 = Int32()
        msg_s2.data = self.servo_s2
        self.servo_s2_pub.publish(msg_s2)

        self.get_logger().info(
            f"Servo - s1: {self.servo_s1}d, s2: {self.servo_s2}d"
        )

    # =========================
    # STOP ROBOT NOW
    # =========================
    def stop_robot_now(self):
        twist = Twist()
        twist.linear.x = 0.0
        twist.linear.y = 0.0
        twist.linear.z = 0.0
        twist.angular.x = 0.0
        twist.angular.y = 0.0
        twist.angular.z = 0.0

        try:
            self.cmd_vel_pub.publish(twist)
        except Exception:
            pass

    # =========================
    # CLEAN EXIT
    # =========================
    def destroy_node(self):
        self.estop_active = True
        self.auto_mode = False
        self.auto_state = "idle"
        self.active_servo_cmd = None
        self.force_zero_motion()
        self.stop_robot_now()

        try:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        except Exception:
            pass

        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)

    node = MqttRosNode()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

