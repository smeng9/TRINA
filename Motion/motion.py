import os
import signal
import sys
import time
import math
from threading import Thread, Lock, RLock
import threading
import numpy as np
from kinematicController import KinematicController
import TRINAConfig #network configs and other configs
from motionStates import * #state structures
from copy import deepcopy,copy
from klampt.math import vectorops,so3
# from klampt import vis
from klampt.model import ik, collide
import numpy as np
from klampt import WorldModel,vis
import os

import logging
from datetime import datetime

if not os.path.exists('errorLogs'):
    os.makedirs('errorLogs')
logger = logging.getLogger(__name__)
# Create handlers
c_handler = logging.StreamHandler()
filename = "errorLogs/logFile_" + datetime.now().strftime('%d%m%Y') + ".log"
f_handler = logging.FileHandler(filename)
c_handler.setLevel(logging.WARNING)
f_handler.setLevel(logging.NOTSET)
# Create formatters and add it to handlers
c_format = logging.Formatter('%(asctime)s %(levelname)s-%(message)s',datefmt='%H:%M:%S')
f_format = logging.Formatter('%(asctime)s %(funcName)s :%(levelname)s- %(message)s',datefmt='%H:%M:%S')
c_handler.setFormatter(c_format)
f_handler.setFormatter(f_format)
# Add handlers to the logger
logger.addHandler(c_handler)
logger.addHandler(f_handler)

class Motion:

    def __init__(self,mode = 'Kinematic', components = ['left_limb','right_limb'], debug_logging = False, codename = 'seed'):
        """
        This class provides a low-level controller to the TRINA robot.

        Parameters
        ------------
        mode: The 'Kinematic' mode starts a kinematic simlation of the robot. The 'Physical' mode interfaces with
            the robotic hardware directly.
        model_path: The TRINA robot model path.
        components: In the physical mode, we would like to have the option of starting only a subset of the components.
            It is a list of component names, including: left_limb, right_limb, base, torso (including the support legs.),
            left_gripper, right_gripper.
        """
        self.codename = codename
        self.mode = mode
        self.model_path = "data/TRINA_world_" + self.codename + ".xml"
        self.computation_model_path = "data/TRINA_world_" + self.codename + ".xml"
        self.debug_logging = debug_logging
        if(self.debug_logging):
            self.logging_filename = time.time()
            self.logging_file = 'teleoperation_log/log_file_' + time.strftime('%Y')+'_'+time.strftime('%m')+'_'+time.strftime('%d')+'_'+time.strftime('%H')+'_'+time.strftime('%M')+'_'+time.strftime('%S')+'.csv'

            if(os.path.exists(self.logging_file)):
                pass
            else:
                with open(self.logging_file,'w') as f:
                    f.write('arm|ik_time|collision_check_time|current_position|target_position|iterations|condition_number\r\n')
                    f.close()

            self.log_file = open(self.logging_file,'a')

        #Klampt world and robot and  used for computation
        self.world = WorldModel()
        res = self.world.readFile(self.computation_model_path)
        if not res:
            logger.error('unable to load model')
            raise RuntimeError("unable to load model")

        #Initialize collision detection
        self.collider = collide.WorldCollider(self.world)
        self.robot_model = self.world.robot(0)
        #End-effector links and active dofs used for arm cartesian control and IK
        self.left_EE_link = self.robot_model.link(TRINAConfig.get_left_tool_link_N(self.codename))
        self.left_active_Dofs = TRINAConfig.get_left_active_Dofs(self.codename)
        self.right_EE_link = self.robot_model.link(TRINAConfig.get_right_tool_link_N(self.codename))
        self.right_active_Dofs = TRINAConfig.get_right_active_Dofs(self.codename)
        #UR5 arms need correct gravity vector
        self.currentGravityVector = [0,0,-9.81]

        #Enable some components of the robot
        self.left_limb_enabled = False

        self.right_limb_enabled = False
        self.base_enabled = False
        self.torso_enabled = False
        self.left_gripper_enabled = False
        self.right_gripper_enabled = False

        #Initialize components
        if self.mode == "Kinematic":
            self.left_limb_enabled = True
            self.right_limb_enabled = True
            self.base_enabled = True
            print("initiating Kinematic controller")
            self.simulated_robot = KinematicController(self.model_path,codename = self.codename)
            print("initiated Kinematic controller")

        elif self.mode == "Physical":
            from limbController import LimbController
            for component in components:
                if component == 'left_limb':
                    self.left_limb = LimbController(TRINAConfig.left_limb_address,gripper=TRINAConfig.left_Robotiq,type = TRINAConfig.left_Robotiq_type,\
                        gravity = TRINAConfig.left_limb_gravity_upright,payload = TRINAConfig.left_limb_payload,cog = TRINAConfig.left_limb_cog)
                    self.left_limb_enabled = True
                    logger.debug('left limb enabled')
                elif component == 'right_limb':
                    self.right_limb = LimbController(TRINAConfig.right_limb_address,gripper=TRINAConfig.right_Robotiq,type = TRINAConfig.right_Robotiq_type,\
                        gravity = TRINAConfig.right_limb_gravity_upright,payload = TRINAConfig.right_limb_payload,cog = TRINAConfig.right_limb_cog)
                    self.right_limb_enabled = True
                    logger.debug('right limb enabled')
                elif component == 'base':
                    from baseController import BaseController
                    self.base = BaseController()
                    self.base_enabled = True
                    logger.debug('base enabled')
                elif component == 'torso':
                    from torsoController import TorsoController
                    self.torso = TorsoController()
                    self.torso_enabled = True
                    logger.debug('torso enabled')
                elif component == 'left_gripper':
                    from gripperController import GripperController
                    self.left_gripper = GripperController()
                    self.left_gripper_enabled = True
                    logger.debug('left gripper enabled')
                elif component == 'right_gripper':
                    from gripperController import GripperController
                    self.right_gripper = GripperController()
                    self.right_gripper_enabled = True
                    logger.debug('right gripper enabled')
                else:
                    logger.error('Motion: wrong component name specified')
                    raise RuntimeError('Motion: wrong component name specified')
        else:
            logger.error('Wrong Mode specified')
            raise RuntimeError('Wrong Mode specified')
        self.left_limb_state = LimbState()
        self.right_limb_state = LimbState()

        self.base_state = BaseState()
        self.left_limb_state = LimbState()
        self.right_limb_state = LimbState()
        self.base_state = BaseState()
        self.torso_state = TorsoState()
        self.left_gripper_state = GripperState()

        self.startTime = time.time()
        #time since startup
        self.t = 0
        self.startUp = False
        #Control loop rate
        self.dt = 0.002
        #automatic mode for future
        self.automatic_mode = False
        self.stop_motion_flag = False
        self.stop_motion_sent = False
        self.shut_down_flag = False
        self.cartesian_drive_failure = False
        self._controlLoopLock = RLock()
        #signal.signal(signal.SIGINT, self.sigint_handler) # catch SIGINT (ctrl-c)

    def sigint_handler(self, signum, frame):
        """ Catch Ctrl+C tp shutdown the robot

        """
        assert(signum == signal.SIGINT)
        logger.warning('SIGINT caught...shutting down the api!')
        print("SIGINT caught...shutting down the api!")
        self.shutdown()

    def time(self):
        """Time since the controller has started

        return:
        ---------------
        float: time since the controller has started in secs

        """
        return self.t

    def startup(self):
        """ Starts up all the individual components and the main control thread.

        Each component is started sequentially. After starting, all components stay where they are and
        start updating their states immediately.
        """

        if not self.startUp:
            if self.mode == "Kinematic":
                self.simulated_robot.start()
            elif self.mode == "Physical":
                if self.torso_enabled:
                    self.torso.start()
                    logger.info('Motion: torso started')
                    print("Motoin: torso started")
                if self.base_enabled:
                    self.base.start()
                    logger.info('Motion: base started')
                    print("Motion: base started")
                if self.left_limb_enabled or self.right_limb_enabled:
                    if self.torso_enabled:
                        #TODO read torso position
                        #tilt_angle =
                        pass
                    else:
                        tilt_angle = 0.0
                    #R_tilt = so3.from_axis_angle(([0,1,0],tilt_angle))
                    #R_local_global_left = so3.mul(R_tilt,TRINAConfig.R_local_global_upright_left)
                    #R_local_global_right = so3.mul(R_tilt,TRINAConfig.R_local_global_upright_right)
                    #gravity_left = so3.apply(so3.inv(R_local_global_left),[0,0,-9.81])
                    #gravity_right = so3.apply(so3.inv(R_local_global_right),[0,0,-9.81])
                    #self.left_limb.setGravity(gravity_left)
                    #self.right_limb.setGravity(gravity_right)
                if self.left_limb_enabled:
                    res = self.left_limb.start()
                    time.sleep(1)
                    if res == False:
                        #better to replace this with logger
                        logger.error('left limb start failure.')
                        print("motion.startup(): ERROR, left limb start failure.")
                        return False
                    else:
                        logger.info('left limb started.')
                        print("motion.startup(): left limb started.")
                        self.left_limb_state.sensedq = self.left_limb.getConfig()[0:6]
                        self.left_limb_state.senseddq = self.left_limb.getVelocity()[0:6]
                        self.left_limb_state.sensedWrench =self.left_limb.getWrench()


                if self.right_limb_enabled:
                    res = self.right_limb.start()
                    time.sleep(1)
                    if res == False:
                        #better to replace this with logger
                        logger.error('right limb start failure.')
                        print("motion.startup(): ERROR, right limb start failure.")
                        return False
                    else:
                        logger.info('right limb started.')
                        print("motion.startup(): right limb started.")
                        self.right_limb_state.sensedq = self.right_limb.getConfig()[0:6]
                        self.right_limb_state.senseddq = self.right_limb.getVelocity()[0:6]
                        self.right_limb_state.sensedWrench = self.right_limb.getWrench()
                if self.left_gripper_enabled:
                    self.left_gripper.start()
                    print('left gripper started')
                    logger.info('left gripper started')
                if self.right_gripper_enabled:
                    self.right_gripper.start()
                    logger.info('right gripper started')


            controlThread = threading.Thread(target = self._controlLoop)
            self.shut_down_flag = False
            self.startUp = True
            controlThread.start()
            logger.info('robot started.')
            print("motion.startup():robot started")


        else:
            ##warning
            logger.warning('Already started.')
            print("motion.startup():Already started")
        return self.startUp

    def _controlLoop(self):
        """main control thread, synchronizing all components
        in each loop,states are updated and new commands are issued
        """

        counter = 0
        self.robot_start_time = time.time()
        logger.info('controlLoop started.')
        print("motion.controlLoop(): controlLoop started.")
        while not self.shut_down_flag:
            loopStartTime = time.time()
            self.t = time.time() - self.startTime
            ###lock the thread
            self._controlLoopLock.acquire()
            if self.mode == "Physical":
                if self.stop_motion_flag:
                    if not self.stop_motion_sent: #send only once to avoid drifting...
                        if self.torso_enabled:
                            self.torso.stopMotion()
                        if self.base_enabled:
                            self.base.stopMotion()
                        if self.left_limb_enabled:
                            self.left_limb.stopMotion()
                        if self.right_limb_enabled:
                            self.right_limb.stopMotion()
                        if self.left_gripper_enabled:
                            self.left_gripper.stopMotion()
                        if self.right_gripper_enabled:
                            self.right_gripper.stopMotion()
                        self.stop_motion_sent = True #unused
                else:
                    #Update current state. Only read state if a new one has been posted
                    if self.base_enabled and self.base.newState():
                        self.base_state.measuredVel = self.base.getMeasuredVelocity()
                        self.base_state.measuredPos = self.base.getPosition()
                        self.base.markRead()

                    if self.torso_enabled and self.torso.newState():
                        tilt, height, _, _ = self.torso.getStates()
                        self.torso_state.measuredTilt = tilt
                        self.torso_state.measuredHeight = height
                        self.torso.markRead()

                    if self.left_limb_enabled and self.left_limb.newState():
                        self.left_limb_state.sensedq = self.left_limb.getConfig()[0:6]
                        self.left_limb_state.senseddq = self.left_limb.getVelocity()[0:6]
                        self.left_limb_state.sensedWrench =self.left_limb.getWrench()
                        self.left_limb.markRead()
                    if self.right_limb_enabled and self.right_limb.newState():
                        self.right_limb_state.sensedq = self.right_limb.getConfig()[0:6]
                        self.right_limb_state.senseddq = self.right_limb.getVelocity()[0:6]
                        self.right_limb_state.sensedWrench = self.right_limb.getWrench()
                        self.right_limb.markRead()

                    if self.left_gripper_enabled and self.left_gripper.newState():
                       self.left_gripper_state.sense_finger_set = self.left_gripper.sense_finger_set
                       self.left_gripper.mark_read()
                    #Send Commands
                    if self.left_limb_enabled:
                        #debug
                        #print(self.left_limb_state.impedanceControl)

                        if self.left_limb_state.commandQueue:
                            if self.left_limb_state.commandType == 0:
                                tmp = time.time() - self.left_limb_state.commandQueueTime
                                if tmp <= self.left_limb_state.commandedQueueDuration:
                                    #? what is the simulated robot doing here...
                                    #self.simulated_robot.setLeftLimbConfig(vectorops.add(self.left_limb_state.commandedqQueueStart,vectorops.mul(self.left_limb_state.difference,tmp/self.left_limb_state.commandedQueueDuration)))
                                    self.left_limb.setConfig(vectorops.add(self.left_limb_state.commandedqQueueStart,vectorops.mul(self.left_limb_state.difference,tmp/self.left_limb_state.commandedQueueDuration)))
                                else:
                                    self.left_limb.setConfig(vectorops.add(self.left_limb_state.commandedqQueueStart,vectorops.mul(self.left_limb_state.difference,1.0)))
                                    #self.simulated_robot.setLeftLimbConfig(vectorops.add(self.left_limb_state.commandedqQueueStart,vectorops.mul(self.left_limb_state.difference,1.0)))
                                    self.setLeftLimbPosition(vectorops.add(self.left_limb_state.commandedqQueueStart,vectorops.mul(self.left_limb_state.difference,1.0)))
                        #### cartesian drive mode
                        elif self.left_limb_state.cartesianDrive:
                            flag = 1
                            while flag == 1:
                                res, target_config = self._left_limb_cartesian_drive(self.left_limb_state.driveTransform)
                                if res == 0:
                                    #res = 0 means IK has failed completely, 1 means keep trying smaller steps, 2 means success
                                    #set to position mode...
                                    self.cartesian_drive_failure = True
                                    self.left_limb_state.commandSent = False
                                    self.left_limb_state.commandedq = deepcopy(self.sensedLeftLimbPosition())
                                    self.left_limb_state.commandeddq = []
                                    self.left_limb_state.commandType = 0
                                    self.left_limb_state.commandQueue = False
                                    self.left_limb_state.commandedqQueue = []
                                    self.left_limb_state.cartesianDrive = False
                                    break
                                elif res == 1:
                                    flag = 1
                                elif res == 2:
                                    flag = 0
                                    self.left_limb.setConfig(target_config)
                        elif self.left_limb_state.impedanceControl:
                            res,target_config = self._left_limb_imdepance_drive()
                            if res == 0:
                                self.cartesian_drive_failure = True
                                self.left_limb_state.commandSent = False
                                self.left_limb_state.commandedq = deepcopy(self.sensedLeftLimbPosition())
                                self.left_limb_state.commandeddq = []
                                self.left_limb_state.commandType = 0
                                self.left_limb_state.commandQueue = False
                                self.left_limb_state.commandedqQueue = []
                                self.left_limb_state.impedanceControl = False
                                self.left_limb_state.Xs = []
                            elif res == 1:
                                self.left_limb.setConfig(target_config)

                        else:
                            if not self.left_limb_state.commandSent:
                                ###setting position will clear velocity commands
                                if self.left_limb_state.commandType == 0:
                                    self.left_limb.setConfig(self.left_limb_state.commandedq)
                                elif self.left_limb_state.commandType == 1:
                                    self.left_limb.setVelocity(self.left_limb_state.commandeddq)
                                self.left_limb_state.commandSent = True
                    if self.right_limb_enabled:
                        if self.right_limb_state.commandQueue:
                            if self.right_limb_state.commandType == 0:
                                tmp = time.time() - self.right_limb_state.commandQueueTime
                                if tmp <= self.right_limb_state.commandedQueueDuration:
                                    #self.simulated_robot.setRightLimbConfig(vectorops.add(self.right_limb_state.commandedqQueueStart,vectorops.mul(self.right_limb_state.difference,tmp/self.right_limb_state.commandedQueueDuration)))
                                    self.right_limb.setConfig(vectorops.add(self.right_limb_state.commandedqQueueStart,vectorops.mul(self.right_limb_state.difference,tmp/self.right_limb_state.commandedQueueDuration)))
                                else:
                                    #self.simulated_robot.setRightLimbConfig(vectorops.add(self.right_limb_state.commandedqQueueStart,vectorops.mul(self.right_limb_state.difference,1.0)))
                                    self.right_limb.setConfig(vectorops.add(self.right_limb_state.commandedqQueueStart,vectorops.mul(self.right_limb_state.difference,1.0)))
                                    self.setRightLimbPosition(vectorops.add(self.right_limb_state.commandedqQueueStart,vectorops.mul(self.right_limb_state.difference,1.0)))
                        elif self.right_limb_state.cartesianDrive:
                            flag = 1
                            while flag:
                                #res = 0 means IK has failed completely, 1 means keep trying smaller steps, 2 means success
                                res, target_config = self._right_limb_cartesian_drive(self.right_limb_state.driveTransform)
                                if res == 0:
                                    #set to position mode...
                                    self.cartesian_drive_failure = True
                                    self.right_limb_state.commandSent = False
                                    self.right_limb_state.commandedq = deepcopy(self.sensedRightLimbPosition())
                                    self.right_limb_state.commandeddq = []
                                    self.right_limb_state.commandType = 0
                                    self.right_limb_state.commandQueue = False
                                    self.right_limb_state.commandedqQueue = []
                                    self.right_limb_state.cartesianDrive = False
                                    break
                                elif res == 1:
                                    flag = 1
                                elif res == 2:
                                    flag = 0
                                    self.right_limb.setConfig(target_config)

                        elif self.right_limb_state.impedanceControl:
                            res,target_config = self._right_limb_imdepance_drive()
                            if res == 0:
                                self.cartesian_drive_failure = True
                                self.right_limb_state.commandSent = False
                                self.right_limb_state.commandedq = deepcopy(self.sensedRightLimbPosition())
                                self.right_limb_state.commandeddq = []
                                self.right_limb_state.commandType = 0
                                self.right_limb_state.commandQueue = False
                                self.right_limb_state.commandedqQueue = []
                                self.right_limb_state.impedanceControl = False
                                self.right_limb_state.Xs = []
                            elif res == 1:
                                self.right_limb.setConfig(target_config)
                        else:
                            if not self.right_limb_state.commandSent:
                                ###setting position will clear velocity commands
                                if self.right_limb_state.commandType == 0:
                                    self.right_limb.setConfig(self.right_limb_state.commandedq)
                                elif self.right_limb_state.commandType == 1:
                                    self.right_limb.setVelocity(self.right_limb_state.commandeddq)
                                self.right_limb_state.commandSent = True

                    #TODO:Base add set path later
                    if self.base_enabled:
                        if self.base_state.commandType == 1:
                            self.base.setCommandedVelocity(self.base_state.commandedVel)
                        elif self.base_state.commandType == 0 and not base_state.commandSent:
                            self.base_state.commandSent = True
                            self.base.setTargetPosition(self.base_state.commandedVel)
                    if self.torso_enabled:
                        if not self.torso_state.commandSent:
                           self.torso_state.commandSent = True
                           self.torso.setTargetPositions(self.torso_state.commandedHeight, self.torso_state.commandedTilt)

                    if self.left_gripper_enabled:
                        if self.left_gripper_state.commandType == 0:
                          self.left_gripper.setPose(self.left_gripper_state.command_finger_set)
                        elif self.left_gripper_state.commandType == 1:
                          self.left_gripper.setVelocity(self.left_gripper_state.command_finger_set)

                    if self.right_gripper_enabled:
                        if self.right_gripper_state.commandType == 0:
                            self.right_gripper.setPose(self.right_gripper_state.command_finger_set)
                        elif self.right_gripper_state.commandType == 1:
                            self.right_gripper.setVelocity(self.right_gripper_state.command_finger_set)
                    #update internal robot model, does not use the base's position and orientation
                    #basically assumes that the world frame is the frame centered at the base local frame, on the floor.
                    robot_model_Q = TRINAConfig.get_klampt_model_q(self.codename,left_limb = self.left_limb_state.sensedq, right_limb = self.right_limb_state.sensedq)
                    #robot_model_Q = [0]*3 + [0]*7 +self.left_limb_state.sensedq+[0]*4+self.right_limb_state.sensedq+[0]*2
                    self.robot_model.setConfig(robot_model_Q)

            elif self.mode == "Kinematic":
                if self.stop_motion_flag:
                    self.simulated_robot.stopMotion()
                else:
                    if self.simulated_robot.newState():
                        self.left_limb_state.sensedq = self.simulated_robot.getLeftLimbConfig()[0:6]
                        self.left_limb_state.senseddq = self.simulated_robot.getLeftLimbVelocity()[0:6]
                        self.left_limb_state.sensedWrench = [0.0]*6
                        self.right_limb_state.sensedq = self.simulated_robot.getRightLimbConfig()[0:6]
                        self.right_limb_state.senseddq = self.simulated_robot.getRightLimbVelocity()[0:6]
                        self.right_limb_state.sensedWrench = [0.0]*6
                        self.base_state.measuredVel = self.simulated_robot.getBaseVelocity()
                        self.base_state.measuredPos = self.simulated_robot.getBaseConfig()
                        #self.left_gripper_state.sense_finger_set = selfprint("motion.controlLoop(): controlLoop started.")

                    ###left limb
                    if self.left_limb_state.commandQueue:
                        if self.left_limb_state.commandType == 0:
                            tmp = time.time() - self.left_limb_state.commandQueueTime
                            if tmp <= self.left_limb_state.commandedQueueDuration:
                                self.simulated_robot.setLeftLimbConfig(vectorops.add(self.left_limb_state.commandedqQueueStart,vectorops.mul(self.left_limb_state.difference,tmp/self.left_limb_state.commandedQueueDuration)))
                            else:
                                self.simulated_robot.setLeftLimbConfig(vectorops.add(self.left_limb_state.commandedqQueueStart,vectorops.mul(self.left_limb_state.difference,1.0)))
                                self.setLeftLimbPosition(vectorops.add(self.left_limb_state.commandedqQueueStart,vectorops.mul(self.left_limb_state.difference,1.0)))

                        #elif self.left_limb_state.commandType == 1:
                        #    if len(self.left_limb_state.commandeddqQueue) > 0:
                        #        #if ((time.time() - self.left_limb_state.lastCommandQueueTime) > TRINAConfig.simulated_robot_control_rate):
                        #        self.simulated_robot.setLeftLimbVelocity(self.left_limb_state.commandeddqQueue.pop(0))
                        #        #self.left_limb_state.lastCommandQueueTime = time.time()


                    #### cartesian drive mode
                    elif self.left_limb_state.cartesianDrive:
                        #clock1 = time.time()
                        flag = 1
                        while flag:
                            res, target_config = self._left_limb_cartesian_drive(self.left_limb_state.driveTransform)
                            if res == 0:
                                #set to position mode...
                                self.cartesian_drive_failure = True
                                self.left_limb_state.commandSent = False
                                self.left_limb_state.commandedq = deepcopy(self.sensedLeftLimbPosition())
                                self.left_limb_state.commandeddq = []
                                self.left_limb_state.commandType = 0
                                self.left_limb_state.commandQueue = False
                                self.left_limb_state.difference = []
                                self.left_limb_state.commandedqQueueStart = []
                                self.left_limb_state.commandQueueTime = 0.0
                                self.left_limb_state.commandedQueueDuration = 0.0
                                self.left_limb_state.cartesianDrive = False
                                break
                            elif res == 1:
                                flag = 1
                            elif res == 2:
                                flag = 0
                                self.simulated_robot.setLeftLimbConfig(target_config)


                    else:
                        if not self.left_limb_state.commandSent:
                            ###setting position will clear velocity commands
                            if self.left_limb_state.commandType == 0:
                                self.simulated_robot.setLeftLimbConfig(self.left_limb_state.commandedq)
                            elif self.left_limb_state.commandType == 1:
                                self.simulated_robot.setLeftLimbVelocity(self.left_limb_state.commandeddq)
                            self.left_limb_state.commandSent = True

                    ##right limb
                    if self.right_limb_state.commandQueue:
                        if self.right_limb_state.commandType == 0:
                            tmp = time.time() - self.right_limb_state.commandQueueTime
                            if tmp <= self.right_limb_state.commandedQueueDuration:
                                self.simulated_robot.setRightLimbConfig(vectorops.add(self.right_limb_state.commandedqQueueStart,vectorops.mul(self.right_limb_state.difference,tmp/self.right_limb_state.commandedQueueDuration)))
                            else:
                                self.simulated_robot.setRightLimbConfig(vectorops.add(self.right_limb_state.commandedqQueueStart,vectorops.mul(self.right_limb_state.difference,1.0)))
                                self.setRightLimbPosition(vectorops.add(self.right_limb_state.commandedqQueueStart,vectorops.mul(self.right_limb_state.difference,1.0)))

                    elif self.right_limb_state.cartesianDrive:
                        flag = 1
                        while flag:
                            #res = 0 means IK has failed completely, 1 means keep trying smaller steps, 2 means success
                            res, target_config = self._right_limb_cartesian_drive(self.right_limb_state.driveTransform)
                            if res == 0:
                                #set to position mode...
                                self.cartesian_drive_failure = True
                                self.right_limb_state.commandSent = False
                                self.right_limb_state.commandedq = deepcopy(self.sensedRightLimbPosition())
                                self.right_limb_state.commandeddq = []
                                self.right_limb_state.commandType = 0
                                self.right_limb_state.commandQueue = False
                                self.right_limb_state.difference = []
                                self.right_limb_state.commandedqQueueStart = []
                                self.right_limb_state.commandQueueTime = 0.0
                                self.right_limb_state.commandedQueueDuration = 0.0
                                self.right_limb_state.cartesianDrive = False
                                break
                            elif res == 1:
                                flag = 1
                            elif res == 2:
                                flag = 0
                                self.simulated_robot.setRightLimbConfig(target_config)

                    else:
                        if not self.right_limb_state.commandSent:
                            ###setting position will clear velocity commands
                            if self.right_limb_state.commandType == 0:
                                self.simulated_robot.setRightLimbConfig(self.right_limb_state.commandedq)
                            elif self.right_limb_state.commandType == 1:
                                self.simulated_robot.setRightLimbVelocity(self.right_limb_state.commandeddq)
                            self.right_limb_state.commandSent = True

                    if self.base_state.commandType == 1:
                        self.simulated_robot.setBaseVelocity(self.base_state.commandedVel)
                    elif self.base_state.commandType == 0 and not base_state.commandSent:
                        base_state.commandSent = True
                        self.base.setTargetPosition(self.base_state.commandedVel)

                    ##gripper
                    self.simulated_robot.setLeftGripperPosition(self.left_gripper_state.command_finger_set)
                    robot_model_Q = TRINAConfig.get_klampt_model_q(self.codename,left_limb = self.left_limb_state.sensedq, right_limb = self.right_limb_state.sensedq)
                    self.robot_model.setConfig(robot_model_Q)

                    #not needed any more
                    #If first loop, do stuff for correcting the wrench
                    # if self.left_limb_state.first_loop:
                    #     (self.left_limb_state.initial_R,_) = self.sensedLeftEETransform() #EE in the global frame
                    #     self.left_limb_state.first_loop = False
                    #
                    #     #TODO change this when torso is actuated
                    #     tilt_angle = 0.0
                    #     R_tilt = so3.from_axis_angle(([0,1,0],tilt_angle))
                    #     R_base_global_left = so3.mul(R_tilt,TRINAConfig.R_local_global_upright_left) #Robot's base frame in global frame
                    #     R_global_base_left = so3.inv(R_base_global_left)
                    #
                    #     G = [0,0,-TRINAConfig.left_limb_payload*9.81] #global frame
                    #     G_base = so3.apply(R_global_base_left,G) #Base frame
                    #
                    #     R_EE_base_left = so3.mul(R_global_base_left,self.left_limb_state.initial_R)
                    #     r = so3.apply(R_EE_base_left,TRINAConfig.left_limb_cog)#CoM in the EE frame, expressed in the robot base frame
                    #     tau = vectorops.cross(r,G_base)
                    #
                    #     self.left_limb_state.wrench_offset = copy(G_base + tau)#in the robot base frame


            self._controlLoopLock.release()
            elapsedTime = time.time() - loopStartTime
            self.t = time.time() - self.startTime
            if elapsedTime < self.dt:
                time.sleep(self.dt-elapsedTime)
            else:
                pass
        logger.info('controlThread exited.')
        print("motion.controlThread: exited")

    #TODO: finish setting the entire robot
    def setPosition(self,q):
        """set the position of the entire robot

        Parameter:
        ---------------
        q: a merged list of joint positions, in the order of torso,base,left limb, right limb, left gripper...
        """
        #assert len(q) == 12, "motion.setPosition(): Wrong number of dimensions of config sent"
        #self.setLeftLimbPosition(q[0:6])
        #self.setRightLimbPosition(q[6:12])
        pass
        return
    def setLeftLimbPosition(self,q):
        """Set the left limb joint positions, and the limb moves as fast as possible

        This will clear the motion queue.

        Parameter:
        --------------
        q: a list of 6 doubles. The desired joint positions.
        """
        logger.debug('number of joint positions sent : %d', len(q))
        assert len(q) == 6, "motion.setLeftLimbPosition(): Wrong number of joint positions sent"('controlThread exited.')
        if self.left_limb_enabled:
            self._controlLoopLock.acquire()
            self._check_collision_linear_adaptive(self.robot_model,self._get_klampt_q(left_limb = self.left_limb_state.sensedq),self._get_klampt_q(left_limb = q))
            self.left_limb_state.commandSent = False
            self.left_limb_state.commandedq = deepcopy(q)
            self.left_limb_state.commandeddq = []
            self.left_limb_state.commandType = 0
            self.left_limb_state.commandQueue = False
            self.left_limb_state.difference = []
            self.left_limb_state.commandedqQueueStart = []
            self.left_limb_state.commandQueueTime = 0.0
            self.left_limb_state.commandedQueueDuration = 0.0
            self.left_limb_state.cartesianDrive = False
            self.left_limb_state.impedanceControl = False
            self.left_limb_state.Xs = []
            self._controlLoopLock.release()
        else:
            logger.warning('Left limb not enabled')
            print("motion.setLeftLimbPosition():Left limb not enabled")
        return

    def setRightLimbPosition(self,q):
        """Set the right limb joint positions, and the limb moves as fast as possible

        This will clear the motion queue.

        Parameter:
        --------------
        q: a list of 6 doubles. The desired joint positions.
        """
        logger.debug('number of joint positions sent : %d', len(q))
        assert len(q) == 6, "motion.setLeftLimbPosition(): Wrong number of joint positions sent"
        if self.right_limb_enabled:
            self._controlLoopLock.acquire()
            self._check_collision_linear_adaptive(self.robot_model,self._get_klampt_q(right_limb = self.right_limb_state.sensedq),self._get_klampt_q(right_limb = q))
            self.right_limb_state.commandSent = False
            self.right_limb_state.commandedq = deepcopy(q)
            self.right_limb_state.commandeddq = []
            self.right_limb_state.commandType = 0
            self.right_limb_state.commandQueue = False
            self.right_limb_state.difference = []
            self.right_limb_state.commandedqQueueStart = []
            self.right_limb_state.commandQueueTime = 0.0
            self.right_limb_state.commandedQueueDuration = 0.0
            self.right_limb_state.cartesianDrive = False
            self.right_limb_state.impedanceControl = False
            self.right_limb_state.Xs = []
            self._controlLoopLock.release()
        else:
            logger.warning('Right limb not enabled')
            print("motion.setRightLimbPosition():Right limb not enabled")
        return

    def setLeftLimbPositionLinear(self,q,duration):
        """Set Left limb to moves to a configuration in a certain amount of time at constant speed

        Set a motion queue, this will clear the setPosition() commands

        Parameters:
        ----------------
        q: a list of 6 doubles. The desired joint positions.
        duration: double. The desired duration.
        """
        logger.debug('number of joint positions sent : %d and duration is %d', len(q), duration)
        assert len(q) == 6, "motion.setLeftLimbPositionLinear(): Wrong number of joint positions sent"
        assert duration > 0, "motion.setLeftLimbPositionLinear(): Duration needs to be a positive number"
        #TODO:add velocity check. Maybe not be able to complete the motion within the duration"
        #TODO:Also collision checks
        if self.left_limb_enabled:
            self._controlLoopLock.acquire()
            self._check_collision_linear_adaptive(self.robot_model,self._get_klampt_q(left_limb = self.left_limb_state.sensedq),self._get_klampt_q(left_limb = q))
            #planningTime = 0.0 + TRINAConfig.ur5e_control_rate
            #positionQueue = []
            #currentq = self.left_limb_state.sensedq
            #difference = vectorops.sub(q,currentq)
            #while planningTime < duration:
            #    positionQueue.append(vectorops.add(currentq,vectorops.mul(difference,planningTime/duration)))
            #    planningTime = planningTime + self.dt #TRINAConfig.ur5e_control_rate
            #positionQueue.append(q)
            self.left_limb_state.commandSent = False
            self.left_limb_state.commandType = 0
            self.left_limb_state.difference = vectorops.sub(q,self.left_limb_state.sensedq)
            self.left_limb_state.commandedqQueueStart = deepcopy(self.left_limb_state.sensedq)
            self.left_limb_state.commandQueue = True
            self.left_limb_state.commandedq = []
            self.left_limb_state.commandeddq = []
            self.left_limb_state.cartesianDrive = False
            self.left_limb_state.impedanceControl = False
            self.left_limb_state.Xs = []
            self.left_limb_state.commandedQueueDuration = duration
            self.left_limb_state.commandQueueTime = time.time()
            self._controlLoopLock.release()
        else:
            logger.warning('Left limb not enabled')
            print("motion.setLeftLimbPosition():Left limb not enabled")
        print

    def setRightLimbPositionLinear(self,q,duration):
        """Set right limb to moves to a configuration in a certain amount of time at constant speed

        Set a motion queue, this will clear the setPosition() commands

        Parameters:
        ----------------
        q: a list of 6 doubles. The desired joint positions.
        duration: double. The desired duration.
        """
        logger.debug('number of joint positions sent : %d and duration is %d', len(q), duration)
        assert len(q) == 6, "motion.setRightLimbPositionLinear(): Wrong number of joint positions sent"
        assert duration > 0, "motion.setRightLimbPositionLinear(): Duration needs to be a positive number"
        #TODO:add velocity check. Maybe not be able to complete the motion within the duration"
        #Also collision checks
        if self.right_limb_enabled:
            self._controlLoopLock.acquire()
            self._check_collision_linear_adaptive(self.robot_model,self._get_klampt_q(right_limb = self.right_limb_state.sensedq),self._get_klampt_q(right_limb = q))
            self.right_limb_state.commandSent = False
            self.right_limb_state.commandType = 0
            self.right_limb_state.difference = vectorops.sub(q,self.right_limb_state.sensedq)
            self.right_limb_state.commandedqQueueStart = deepcopy(self.right_limb_state.sensedq)
            self.right_limb_state.commandQueue = True
            self.right_limb_state.commandedq = []
            self.right_limb_state.commandeddq = []
            self.right_limb_state.cartesianDrive = False
            self.right_limb_state.impedanceControl = False
            self.right_limb_state.Xs = []
            self.right_limb_state.commandedQueueDuration = duration
            self.right_limb_state.commandQueueTime = time.time()
            self._controlLoopLock.release()
        else:
            logger.warning('Right limb not enabled')
            print("motion.setRightLimbPosition():Right limb not enabled")
        return




    def sensedLeftLimbPosition(self):
        """The current joint positions of the left limb

        Return:
        --------------
        A list of 6 doubles. The limb configuration.
        """
        if self.left_limb_enabled:
            return copy(self.left_limb_state.sensedq)
        else:
            logger.warning('left limb not enabled')
            print("motion().sensedLeftLimbPosition: left limb not enabled")
            return [0]*6

    def sensedRightLimbPosition(self):
        """The current joint positions of the right limb

        Return:
        --------------
        A list of 6 doubles. The limb configuration.
        """
        if self.right_limb_enabled:
            return copy(self.right_limb_state.sensedq)
        else:
            logger.warning('Right limb not enabled')
            print("motion().sensedRightLimbPosition: right limb not enabled")
            return [0]*6
    def setVelocity(self,qdot):
        """set the velocity of the entire robot, under development rn

        """
        #assert len(qdot) == 12, "motion.setPosition(): Wrong number of dimensions of config sent"
        #self.setLeftLimbVelocity(qdot[0:6])
        #self.setRightLimbVelcity(qdot[6:12])
        pass
        return

    def setLeftLimbVelocity(self,qdot):
        """Set the left limb joint velocities

        Parameter:
        ----------------
        qdot: a list of 6 doubles. Joint velocities
        """
        if self.left_limb_enabled:
            logger.debug('number of joint velocities sent : %d', len(qdot))
            assert len(qdot) == 6, "motion.setLeftLimbVelocity()): Wrong number of joint velocities sent"
            self._controlLoopLock.acquire()
            self.left_limb_state.commandSent = False
            self.left_limb_state.commandeddq = deepcopy(qdot)
            self.left_limb_state.commandedq = []
            self.left_limb_state.commandType = 1
            self.left_limb_state.commandQueue = False
            self.left_limb_state.difference = []
            self.left_limb_state.commandedqQueueStart = []
            self.left_limb_state.commandQueueTime = 0.0
            self.left_limb_state.commandedQueueDuration = 0.0
            self.left_limb_state.cartesianDrive = False
            self.left_limb_state.impedanceControl = False
            self.left_limb_state.Xs = []
            self._controlLoopLock.release()
        else:
            logger.warning('Left limb not enabled')
            print("Left limb not enabled")

        return

    def setRightLimbVelocity(self,qdot):
        """Set the right limb joint velocities

        Parameter:
        ----------------
        qdot: a list of 6 doubles. Joint velocities
        """
        if self.right_limb_enabled:
            logger.debug('number of joint velocities sent : %d', len(qdot))
            assert len(qdot) == 6, "motion.setRightLimbVelocity()): Wrong number of joint velocities sent"
            self._controlLoopLock.acquire()
            self.right_limb_state.commandSent = False
            self.right_limb_state.commandeddq = deepcopy(qdot)
            self.right_limb_state.commandedq = []
            self.right_limb_state.commandType = 1
            self.right_limb_state.commandQueue = False
            self.right_limb_state.difference = []
            self.right_limb_state.commandedqQueueStart = []
            self.right_limb_state.commandQueueTime = 0.0
            self.right_limb_state.commandedQueueDuration = 0.0
            self.right_limb_state.cartesianDrive = False
            self.right_limb_state.impedanceControl = False
            self._controlLoopLock.release()
        else:
            logger.warning('Right limb not enabled')
            print("Right limb not enabled.")
        return

    def setLeftEEInertialTransform(self,Ttarget,duration):
        """Set the trasform of the arm w.r.t. the base frame, movement complsetLeftLimbCo
        """
        start_time = time.time()
        if self.left_limb_enabled:
            self._controlLoopLock.acquire()

            initial = self.robot_model.getConfig()
            #initial = [0]*3 + [0]*7 +self.left_limb_state.sensedq+[0]*11+self.right_limb_state.sensedq+[0]*10
            #self.robot_model.setConfig(initial)

            goal = ik.objective(self.left_EE_link,R=Ttarget[0],t = Ttarget[1])
            if ik.solve(goal,activeDofs = self.left_active_Dofs):
            # if ik.solve_nearby(goal,maxDeviation=3,activeDofs = self.left_active_Dofs):
            #if result:
                target_config = self.robot_model.getConfig()
                logger.info('IK solve successful')
                print("motion.setLeftEEInertialTransform():IK solve successful")
            else:
                self._controlLoopLock.release()
                #"warning"
                logger.warning('IK solve failure: no IK solution found')
                print('motion.setLeftEEInertialtransform():IK solve failure: no IK solution found')
                return 'motion.setLeftEEInertialtransform():IK solve failure: no IK solution found'
            ik_solve_time = time.time() -start_time
            # print("Solving IK takes", time.time() -start_time,' and {} iterations'.format(iterations))
            start_time_2 = time.time()
            res = self._check_collision_linear_adaptive(self.robot_model,initial,target_config)
            col_check_time = time.time()-start_time_2
            # print("collision checking takes", time.time() - start_time_2)
            #print(res)
            if res:
                self._controlLoopLock.release()
                logger.warning('Self-collision midway')
                print('motion.setLeftEEInertialtransform():Self-collision midway')
                return 'motion.setLeftEEInertialtransform():Self-collision midway'
            else:
                logger.info('No collision')
                print("motion.setLeftEEInertialTransform():No collision")

            self.robot_model.setConfig(initial)
            self._controlLoopLock.release()
            start_time = time.time()
            self.setLeftLimbPositionLinear(target_config[self.left_active_Dofs[0]:self.left_active_Dofs[5]+1],duration)
            # print("setting linear position takes", time.time() - start_time)
        else:
            logger.warning('Left limb not enabled')
            print("Left limb not enabled.")
        return ''

    def setLeftEEVelocity(self,v, tool = [0,0,0]):
        """Set the end-effect cartesian velocity, in the base frame.

        Implemented using position control and IK. Will keep moving until infeasible.
        TODO: implement collision detection

        Parameter:
        --------------
        v: A list of 6 doubled. v[0:3] is the desired cartesian position velocities and v[3:6] is the desired rotational velocity

        """
        if self.left_limb_enabled:
            self._controlLoopLock.acquire()
            self.left_limb_state.impedanceControl = False
            self.left_limb_state.Xs = []
            if not self.left_limb_state.cartesianDrive:
                self.left_limb_state.commandedq = []
                self.left_limb_state.commandeddq = []
                self.left_limb_state.commandQueue = False
                self.left_limb_state.difference = []
                self.left_limb_state.commandedqQueueStart = []
                self.left_limb_state.commandQueueTime = 0.0
                self.left_limb_state.commandedQueueDuration = 0.0
                self.cartesian_drive_failure = False
                ##cartesian velocity drive

                if len(v) == 3:
                    self.left_limb_state.cartesianDriveV = deepcopy(v)
                    self.left_limb_state.cartesianMode = 1

                elif len(v) == 6:
                    self.left_limb_state.cartesianDriveV = deepcopy(v[0:3])
                    self.left_limb_state.cartesianDriveW = deepcopy(v[3:6])
                    self.left_limb_state.cartesianMode = 0
                else:
                    #error
                    logger.error('wrong input')
                    print("motion.setLeftEEVelocity(): wrong input")
                    return

                self.left_limb_state.cartesianDrive = True
                (R,t) = self.sensedLeftEETransform()
                self.left_limb_state.startTransform = (R,vectorops.add(so3.apply(R,tool),t))
                self.left_limb_state.driveTransform = (R,vectorops.add(so3.apply(R,tool),t))
                self.left_limb_state.driveSpeedAdjustment = 1.0
                self.left_limb_state.toolCenter = deepcopy(tool)
                self._controlLoopLock.release()
            else:
                if len(v) == 3:
                    self.left_limb_state.cartesianDriveV = deepcopy(v)
                    self.left_limb_state.cartesianMode = 1

                elif len(v) == 6:
                    self.left_limb_state.cartesianDriveV = deepcopy(v[0:3])
                    self.left_limb_state.cartesianDriveW = deepcopy(v[3:6])
                    self.left_limb_state.cartesianMode = 0
                else:
                    #error
                    logger.error('wrong input')
                    print("motion.setLeftEEVelocity(): wrong input")
                    return

                (R,t) = self.sensedLeftEETransform()
                self.left_limb_state.startTransform = (R,vectorops.add(so3.apply(R,tool),t))
                self.left_limb_state.driveSpeedAdjustment = 1.0
                self.left_limb_state.toolCenter = deepcopy(tool)
                self._controlLoopLock.release()
        else:
            logger.warning('Left limb not enabled')
            print("Left limb not enabled.")

        return ''

    def setRightEEInertialTransform(self,Ttarget,duration):
        """Set the trasform of the arm w.r.t. the base frame, movement complete in a certain amount of time

        This current version assmumes that the torso is at zero position.
        #TODO: implement version with torso not at zero.

        Parameter:
        ---------------
        Ttarget: A klampt rigid transform (R,t). R is a column major form of a rotation matrix. t is a 3-element list
        duration: double. The duration of the movement
        """
        start_time = time.time()

        if self.right_limb_enabled:
            self._controlLoopLock.acquire()
            initial = self.robot_model.getConfig()

            goal = ik.objective(self.right_EE_link,R=Ttarget[0],t = Ttarget[1])
            if ik.solve(goal,activeDofs = self.right_active_Dofs):
                target_config = self.robot_model.getConfig()
                logger.info('IK solve successful')
                print("motion.setRightEEInertialTransform():IK solve successful")
            else:
                self._controlLoopLock.release()
                logger.warning('IK solve failure: no IK solution found')
                print('motion.setRightEEInertialtransform():IK solve failure: no IK solution found')
                return 'motion.setRightEEInertialtransform():IK solve failure: no IK solution found'
            start_time_2 = time.time()
            res = self._check_collision_linear_adaptive(self.robot_model,initial,target_config)
            col_check_time = time.time()-start_time_2

            #print(res)
            if res:
                self._controlLoopLock.release()
                logger.warning('Self-collision midway')
                print('motion.setRighttEEInertialtransform():Self-collision midway')
                return 'motion.setRighttEEInertialtransform():Self-collision midway'
            else:
                logger.info('No collision')
                print("motion.setRightEEInertialTransform():No collision")

            self.robot_model.setConfig(initial)
            self._controlLoopLock.release()
            self.setRightLimbPositionLinear(target_config[self.right_active_Dofs[0]:self.right_active_Dofs[5]+1],duration)
        else:
            logger.warning('Right limb not enabled')
            print("Right limb not enabled. ")
        return ''

    def setRightEEVelocity(self,v, tool = [0,0,0]):
        """Set the end-effect cartesian velocity, in the base frame.

        Implemented using position control and IK. Will keep moving until infeasible.
        TODO: implement collision detection

        Parameter:
        --------------
        v: A list of 6 doubled. v[0:3] is the desired cartesian position velocities and v[3:6] is the desired rotational velocity

        """
        if self.right_limb_enabled:
            self._controlLoopLock.acquire()
            self.right_limb_state.impedanceControl = False
            self.right_limb_state.Xs = []
            if not self.right_limb_state.cartesianDrive:
                self.right_limb_state.commandedq = []
                self.right_limb_state.commandeddq = []
                self.right_limb_state.commandQueue = False
                self.right_limb_state.difference = []
                self.right_limb_state.commandedqQueueStart = []
                self.right_limb_state.commandQueueTime = 0.0
                self.right_limb_state.commandedQueueDuration = 0.0
                self.cartesian_drive_failure = False
                ##cartesian velocity drive
                if len(v) == 3:
                    self.right_limb_state.cartesianDriveV = deepcopy(v)
                    self.right_limb_state.cartesianMode = 1

                elif len(v) == 6:
                    self.right_limb_state.cartesianDriveV = deepcopy(v[0:3])
                    self.right_limb_state.cartesianDriveW = deepcopy(v[3:6])
                    self.right_limb_state.cartesianMode = 0
                else:
                    logger.error('wrong input')
                    print("motion.setRightEEVelocity(): wrong input")
                    return

                self.right_limb_state.cartesianDrive = True
                (R,t) = self.sensedRightEETransform()
                self.right_limb_state.startTransform = (R,vectorops.add(so3.apply(R,tool),t))
                self.right_limb_state.driveTransform = (R,vectorops.add(so3.apply(R,tool),t))
                self.right_limb_state.driveSpeedAdjustment = 1.0
                self.right_limb_state.toolCenter = deepcopy(tool)
                self._controlLoopLock.release()
            else:
                if len(v) == 3:
                    self.right_limb_state.cartesianDriveV = deepcopy(v)
                    self.right_limb_state.cartesianMode = 1

                elif len(v) == 6:
                    self.right_limb_state.cartesianDriveV = deepcopy(v[0:3])
                    self.right_limb_state.cartesianDriveW = deepcopy(v[3:6])
                    self.right_limb_state.cartesianMode = 0
                else:
                    logger.error('wrong input')
                    print("motion.setRightEEVelocity(): wrong input")
                    return

                (R,t) = self.sensedRightEETransform()
                self.right_limb_state.startTransform = (R,vectorops.add(so3.apply(R,tool),t))
                #self.right_limb_state.driveTransform = (R,vectorops.add(so3.apply(R,tool),t))
                self.right_limb_state.driveSpeedAdjustment = 1.0
                self.right_limb_state.toolCenter = deepcopy(tool)
                self._controlLoopLock.release()
        else:
            logger.warning('Right limb not enabled.')
            print("Right limb not enabled.")
        return ''

    def setRightEETransformImpedance(self,Tg,K,tool_center = [0,0,0]):
        """Set the target transform of the EE in the global frame. The EE will follow a linear trajectory to the target transform, following EE velocity limits and
        user-defined elasticity. By elasticity, it means of there is external force on the EE, EE will deviate from the target transform and behaves like a linear spring.
        Currently, damping is NOT implemented.

        Parameters:
        -------------
        Tg: target transform of the EE, in Klampt format
        K: a 6x6 numpy 2D array. The elasticity matrix, normally this should be a diagonal matrix. The ordering is the first 3 are translations.

        Return:
        -------------
        None
        """
        if not self.right_limb_enabled:
            print("SetRightEETransform():right limb is not enabled")
            logger.warning('SetRightEETransform():Right limb not enabled.')
            return

        if self.mode == "Kinematic":
            print("SetRightEETransform():Impedance control not available for Kinematic mode.")
            logger.warning('SetRightEETransform():Impedance control not available for Kinematic mode.')
            return

        #TODO
        #add asserts to check on

        self._controlLoopLock.acquire()
        self.right_limb_state.Tg = copy(Tg)
        self.right_limb_state.mg = Tg[1] + so3.moment(Tg[0])
        self.right_limb_state.K = copy(K)
        self.right_limb_state.Kinv = np.linalg.inv(K)
        self.right_limb_state.toolCenter = tool_center
        self.right_limb_state.cartesianDrive = False
        self.right_limb_state.impedanceControl = True
        self.right_limb_state.Xs = []
        self._controlLoopLock.release()
        return

    def setLeftEETransformImpedance(self,Tg,K,tool_center = [0,0,0]):
        """Set the target transform of the EE in the global frame. The EE will follow a linear trajectory to the target transform, following EE velocity limits and
        user-defined elasticity. By elasticity, it means of there is external force on the EE, EE will deviate from the target transform and behaves like a linear spring.
        Currently, damping is NOT implemented.

        Parameters:
        -------------
        Tg: target transform of the EE, in Klampt format
        K: a 6x6 numpy 2D array. The elasticity matrix, normally this should be a diagonal matrix. The ordering is the first 3 are translations.

        Return:
        -------------
        None
        """
        if not self.left_limb_enabled:
            print("SetLeftEETransform():left limb is not enabled")
            logger.warning('SetLeftEETransform():Left limb not enabled.')
            return

        if self.mode == "Kinematic":
            print("SetLeftEETransform():Impedance control not available for Kinematic mode.")
            logger.warning('SetLeftEETransform():Impedance control not available for Kinematic mode.')
            return

        #TODO
        #add asserts to check on

        self._controlLoopLock.acquire()
        self.left_limb_state.Tg = copy(Tg)
        self.left_limb_state.mg = Tg[1] + so3.moment(Tg[0])
        self.left_limb_state.K = copy(K)
        self.left_limb_state.Kinv = np.linalg.inv(K)
        self.left_limb_state.toolCenter = tool_center
        self.left_limb_state.cartesianDrive = False
        self.left_limb_state.impedanceControl = True
        self.left_limb_state.Xs = [[],[],[],[],[],[]]
        B = np.eye(6)*0.01
        B[3,3] = 0.0005
        B[4,4] = 0.0005
        B[5,5] = 0.0005

        self.left_limb_state.B = B
        m = np.eye(6)*2.0
        m[3,3] = 0.05
        m[4,4] = 0.05
        m[5,5] = 0.05
        self.left_limb_state.m_inv = np.linalg.inv(m)
        self.left_limb_state.N = 50
        self._controlLoopLock.release()
        return

    def sensedLeftEETransform(self,tool_center= [0,0,0]):
        """Return the transform w.r.t. the base frame

        Return:
        -------------
        (R,t)
        """
        if self.left_limb_enabled:
            T = self.left_EE_link.getTransform()
            return (T[0],vectorops.add(T[1],so3.apply(T[0],tool_center)))
        else:
            return
            logger.warning('Left limb not enabled.')
            print("Left limb not enabled.")
            return

    def sensedLeftEEVelocity(self,local_pt = [0,0,0]):
        """Return the EE translational and rotational velocity  w.r.t. the base DataFrame

        Parameter:
        ----------------
        local_pt: the local point in the EE local frame.

        Return:
        ----------------
        (v,w), a tuple of 2 velocity vectors

        """
        if self.left_limb_enabled:
            position_J = np.array(self.left_EE_link.getJacobian(local_pt))
            q_dot = TRINAConfig.get_klampt_model_q(self.codename,left_limb = self.left_limb_state.senseddq)
            EE_vel = np.dot(position_J,q_dot).tolist()
            return ([EE_vel[3],EE_vel[4],EE_vel[5]],[EE_vel[0],EE_vel[1],EE_vel[2]])
        else:
            return "NA"

    def sensedRightEETransform(self):
        """Return the transform w.r.t. the base frame

        Return:
        -------------
        (R,t)
        """
        if self.right_limb_enabled:
            return self.right_EE_link.getTransform()
        else:
            logger.warning('Right limb not enabled.')
            print("Right limb not enabled.")
            return

    def sensedRightEEVelocity(self,local_pt = [0,0,0]):
        """Return the EE translational and rotational velocity  w.r.t. the base DataFrame

        Parameter:
        ----------------
        local_pt: the local point in the EE local frame.

        Return:
        ----------------
        (v,w), a tuple of 2 velocity vectors

        """
        if self.right_limb_enabled:
            position_J = np.array(self.right_EE_link.getJacobian(local_pt))
            q_dot = TRINAConfig.get_klampt_model_q(self.codename,right_limb = self.right_limb_state.senseddq)
            EE_vel = np.dot(position_J,q_dot).tolist()
            return ([EE_vel[3],EE_vel[4],EE_vel[5]],[EE_vel[0],EE_vel[1],EE_vel[2]])
        else:
            return "NA"

    def sensedLeftLimbVelocity(self):
        """ Return the current limb joint velocities

        Return:
        ---------------
        A list of 6 doubles. The joint velocities.
        """
        if self.left_limb_enabled:
            return copy(self.left_limb_state.senseddq)
        else:
            logger.warning('Left limb not enabled.')
            print("Left limb not enabled.")
            return

    def sensedRightLimbVelocity(self):
        """ Return the current limb joint velocities

        Return:
        ---------------
        A list of 6 doubles. The joint velocities.
        """
        if self.right_limb_enabled:
            return copy(self.right_limb_state.senseddq)
        else:
            logger.warning('Right limb not enabled.')
            print('Right limb not enabled.')
            return

    def setBaseTargetPosition(self, q, vel):
        """Set the local target position of the base.

        The base constructs a path to go to the desired position, following the desired speed along the path
        Parameter:
        ---------------
        q: a list of 3 doubles. The desired x,y position and rotation.
        Vel: double. Desired speed along the path.
        """
        if self.base_enabled:
            logger.debug('dimensions : %d', len(q))
            assert len(q) == 3, "motion.setBaseTargetPosition(): wrong dimensions"
            self._controlLoopLock.acquire()
            self.base_state.commandType = 0
            self.base_state.commandedTargetPosition = deepcopy(q)
            self.base_state.pathFollowingVel = vel
            self.base_state.commandSent = False
            self._controlLoopLock.release()
        else:
            logger.warning('Base not enabled.')
            print('Base not enabled.')

    def setBaseVelocity(self, q):
        """Set the velocity of the base relative to the local base frame

        Parameter:
        ---------------
        q: a list of 2 doubles. The linear and rotational velocites.
        """
        if self.base_enabled:
            logger.debug('dimensions : %d', len(q))
            assert len(q) == 2 ,"motion.setBaseVelocity(): wrong dimensions"
            self._controlLoopLock.acquire()
            self.base_state.commandType = 1
            self.base_state.commandedVel = deepcopy(q)
            self.base_state.commandSent = False
            self._controlLoopLock.release()
        else:
            logger.warning('Base not enabled.')
            print('Base not enabled.')

    def setTorsoTargetPosition(self, q):
        """Set the torso target position.

        Moves to the target as fast as possible.

        Parameter:
        --------------
        q: a list of 2 doubles. The lift and tilt positions.
        """
        if self.torso_enabled:
            logger.debug('dimensions : %d', len(q))
            assert len(q) == 2, "motion.SetTorsoTargetPosition(): wrong dimensions"
            height, tilt = q
            self._controlLoopLock.acquire()
            self.torso_state.commandedHeight = height
            self.torso_state.commandedTilt = tilt
            self.torso_state.commandSent = False
            self._controlLoopLock.release()
        else:
            logger.warning('Torso not enabled.')
            print('Torso not enabled.')

    def sensedBaseVelocity(self):
        """Returns the current base velocity

        Return:
        -----------
        A list of 2 doubles. Linear and Rotational velocities.
        """
        if self.base_enabled:
            return copy(self.base_state.measuredVel)
        else:
            logger.warning('Base not enabled.')
            print('Base not enabled')

    def sensedBasePosition(self):
        """Returns the current base position. Zero position is the position when the base is started.

        Return:
        -------------
        A list of 3 doubles. Position and rotation.

        """
        if self.base_enabled:
            return copy(self.base_state.measuredPos)
        else:
            logger.warning('Base not enabled.')
            print('Base not enabled')

    def sensedTorsoPosition(self):
        """Returns the current torso position

        Return:
        -------------
        A list of 2 doubles. The positions.
        """
        if self.torso_enabled:
            return copy([self.torso_state.measuredHeight, self.torso_state.measuredTilt])
        else:
            logger.warning('Torso not enabled.')
            print('Torso not enabled.')

    def openLeftRobotiqGripper(self):
        """ Open the parallel gripper or release the vacuum gripper. This gripper is connected to the arm.
        """
        if self.left_limb_enabled:
            self.left_limb.openGripper()
        else:
            logger.warning('Left limb not enabled.')
            print('Left limb not enabled.')

    def closeLeftRobotiqGripper(self):
        """ close the parallel gripper or start the vacuum gripper. This gripper is connected to the arm.
        """
        if self.left_limb_enabled:
            self.left_limb.closeGripper()
        else:
            logger.warning('Left limb not enabled.')
            print('Left limb not enabled.')

    def openRightRobotiqGripper(self):
        """ Open the parallel gripper or release the vacuum gripper. This gripper is connected to the arm.
        """
        if self.right_limb_enabled:
            self.right_limb.openGripper()
        else:
            logger.warning('Right limb not enabled.')
            print('Right limb not enabled.')

    def closeRightRobotiqGripper(self):
        """ close the parallel gripper or start the vacuum gripper. This gripper is connected to the arm.
        """
        if self.right_limb_enabled:
            self.right_limb.closeGripper()
        else:
            logger.warning('Right limb not enabled.')
            print('Right limb not enabled.')

    def setLeftGripperPosition(self, position):
        """Set the position of the gripper. Moves as fast as possible.

        Parameters:
        -----------------
        position: a list of 4 doubles, the angles of finger 1,2 , the angle of the thumb,
            the rotation of finger 1&2 (they rotate together)
        """
        self._controlLoopLock.acquire()
        self.left_gripper_state.commandType = 0
        self.left_gripper_state.command_finger_set = deepcopy(position)
        self._controlLoopLock.release()

    def setLeftGripperVelocity(self,velocity):
        """Set the velocity of the gripper. Moves as fast as possible.
        #TODO
        ###Under development
        """
        self.left_gripper_state.commandType = 1
        self.left_gripper_state.command_finger_set = deepcopy(velocity)

    def sensedLeftGripperPosition(self):
        """Return the current positions of the fingers.
        #TODO
        ###Under development
        """
        return copy(self.left_gripper_state.sense_finger_set)

    def getKlamptCommandedPosition(self):
        """Return the entire commanded position, in Klampt format. The base returns velocity instead
        """
        if self.left_limb_state.commandedq and self.right_limb_state.commandedq:
            return TRINAConfig.get_klampt_model_q(self.codename,left_limb = self.left_limb_state.commandedq, right_limb = self.right_limb_state.commandedq,base = self.base_state.commandedVel + [0])
        else:
            return self.getKlamptSensedPosition()

    def getKlamptSensedPosition(self):
        """Return the entire sensed Klampt position, in Klampt format.
        """
        #return TRINAConfig.get_klampt_model_q(self.codename,left_limb = self.left_limb_state.sensedq, right_limb = self.right_limb_state.sensedq,base = self.base_state.measuredPos)
        return self.robot_model.getConfig()


    def sensedLeftEEWrench(self,frame = 'global'):
        """
        Parameters:
        ------------------
        frame:  1 = 'global' or 0 = 'local'
        Return:
        ------------------
        wrench: list of 6 floats, expressed either in global or local EE coordinate, gravity of the attachement compensated for

        Note:
        The attachment weight to the ft sensor can be corrected by U5 directly
        """
        if not self.left_limb_enabled:
            logger.info("sensedLeftEEWrench:left limb not enabled")
            return [0,0,0,0,0,0]

        wrench_raw = self.left_limb_state.sensedWrench #this wrench is expressed in the robot base frame
        (R,_) = self.sensedLeftEETransform() #current EE R in global frame
        R_base_global_left = copy(TRINAConfig.R_local_global_upright_left)
        R_global_base_left = so3.inv(R_base_global_left)
        R_EE_base_left = so3.mul(R_global_base_left,R)


        if frame == 'global':
            return list(so3.apply(R_base_global_left,wrench_raw[0:3]) + so3.apply(R_base_global_left,wrench_raw[3:6]))
        elif frame == 'local':
            return list(so3.apply(so3.inv(R_EE_base_left),wrench_raw[0:3]) + so3.apply(so3.inv(R_EE_base_left),wrench_raw[3:6]))

        #not needed any more since UR already does gravity compensation.
        # elif format == 'corrected':
        #     r = so3.aply(R_EE_base_left,TRINAConfig.left_limb_cog) #CoM in the EE frame, expressed in the robot base frame
        #     G = [0,0,-TRINAConfig.left_limb_payload*9.81] #global frame
        #     G_base = so3.apply(R_global_base_left,G) #Base frame
        #     tau = vectorops.cross(r,G_base) #base frame
        #
        #     wrench_0 = vectorops.sub(G_base + tau,self.left_limb_state.wrench_offset) #what the sensor would read if no external wrench
        #
        #     #in the base frame
        #     wrench = vectorops.sub(wrench_raw,wrench_0)
        #     #this is expressed in the global frame
        #     wrench_global = so3.apply(R_base_global_left,wrench[0:3]) + so3.apply(R_base_global_left,wrench[3:6])
        #
        #     if frame == 'global':
        #         return wrench_global
        #     elif frame == 'local':
        #         wrench_EE = so3.apply(so3.inv(R),wrench_global[0:3]) + so3.apply(so3.inv(R),wrench_global[3:6])
        #         return wrench_EE

        return

    def sensedRightEEWrench(self,frame = 'global'):
        """
        Parameters:
        ------------------
        frame:  1 = 'global' or 0 = 'local'
        Return:
        ------------------
        wrench: list of 6 floats, expressed either in global or local EE coordinate

        """
        if not self.right_limb_enabled:
            logger.info("sensedRightEEWrench:right limb not enabled")
            return [0,0,0,0,0,0]
        wrench_raw = self.right_limb_state.sensedWrench #this wrench is expressed in the robot base frame
        (R,_) = self.sensedRightEETransform() #current EE R in global frame
        R_base_global_right = copy(TRINAConfig.R_local_global_upright_right)
        R_global_base_right = so3.inv(R_base_global_right)
        R_EE_base_right = so3.mul(R_global_base_right,R)


        if frame == 'global':
            return list(so3.apply(R_base_global_right,wrench_raw[0:3]) + so3.apply(R_base_global_right,wrench_raw[3:6]))
        elif frame == 'local':
            return list(so3.apply(so3.inv(R_EE_base_right),wrench_raw[0:3]) + so3.apply(so3.inv(R_EE_base_right),wrench_raw[3:6]))




    def shutdown(self):
        """Shutdown the componets.

        """
        self.shut_down_flag = True
        if self.mode == "Physical":
            if self.base_enabled:
                self.base.shutdown()
            if self.torso_enabled:
                self.torso.shutdown()
            if self.left_limb_enabled:
                self.left_limb.stop()
            if self.right_limb_enabled:
                self.right_limb.stop()
            #TODO: integrate gripper code
            if self.left_gripper_enabled:
                self.left_gripper.shutDown()

        elif self.mode == "Kinematic":
            self.simulated_robot.shutdown()

        self.shut_down_flag = True
        self.startUp = False
        return 0

    def isStarted(self):
        """Return whether the robot has started

        Return:
        ------------
        bool
        """
        return self.startUp

    def isShutDown(self):
        """Return whether the robot is shutdown

        Return:
        ------------
        bool
        """
        return self.shut_down_flag

    def moving(self):
        """Returns true if the robot is currently moving.

        Return:
        ------------
        bool
        """

        if self.mode == 'Physical':
            flag = False
            if self.left_limb_enabled:
                flag = flag or self.left_limb.moving()
            if self.right_limb_enabled:
                flag = flag or self.right_limb.moving()
            if self.base_enabled:
                flag = flag or self.base.moving()
            if self.torso_enabled:
                flag = flag or self.torso.moving()
            return flag
        else:
            return self.simulated_robot.moving()

    def mode(self):
        """Returns the current mode. "Kinematic" or "Physical"

        Return:
        ------------
        string
        """
        return self.mode

    def stopMotion(self):
        """Pause the motion of the robot, starting from the next control loop.

        This is not shutdown. Just a pause.
        """
        self.stop_motion_flag = True
        self.stop_motion_sent = False
        self._purge_commands()
        return



    ### ------------------------- ###
    ###current change up to here#######
    def resumeMotion(self):
        """Unpause the robot.

        After unpausing, the robot is still stationery until some new commands is added
        """
        self.base.startMotion()
        self.startMotionFlag = False
        self.left_gripper.resume()
        return

    def mirror_arm_config(self,config):
        """given the Klampt config of the left or right arm, return the other

        Paremeters:
        ---------------
        A list of 6 doubles. Limb configuration.

        Return:
        ---------------
        A list of 6 doubles. Limb configuration.
        """
        RConfig = []
        RConfig.append(-config[0])
        RConfig.append(-config[1]-math.pi)
        RConfig.append(-config[2])
        RConfig.append(-config[3]+math.pi)
        RConfig.append(-config[4])
        RConfig.append(-config[5])

        for ele in RConfig:
            if ele >= 2*math.pi or ele <= -2*math.pi:
                print('out of range..')
                return []
        return RConfig

    def getWorld(self):
        """ Return the simulated robot

        Return:
        -------------
        The Klampt world of the simulated robot.
        """
        if self.mode == "Kinematic":
            return self.simulated_robot.getWorld()
        else:
            print("wrong robot mode.")

    def cartesianDriveFail(self):
        """ Return if cartedian drive has failed or not

        Return:
        ----------------
        bool
        """
        return self.cartesian_drive_failure


    def setRobotToDefualt(self):
        """ Some helper function when debugging"""
        leftUntuckedConfig = [-0.2028,-2.1063,-1.610,3.7165,-0.9622,0.0974]
        rightUntuckedConfig = self.mirror_arm_config(leftUntuckedConfig)
        self.setLeftLimbPositionLinear(leftUntuckedConfig,1)
        self.setRightLimbPositionLinear(rightUntuckedConfig,1)

    def zeroLeftFTSensor(self):
        """
        zero the ft sensor.
        """
        if self.left_limb_enabled:
            self.left_limb.zeroFTSensor()
        else:
            logger.info("zeroLeftFTSensor:left limb not enabled")

        return 0

    def zeroRightFTSensor(self):
        """
        zero the ft sensor.
        """
        if self.right_limb_enabled:
            self.right_limb.zeroFTSensor()
        else:
            logger.info("zeroRightFTSensor:right limb not enabled")

        return 0

    ###Below are internal helper functions
    def _purge_commands(self):
        """Clear all the motion commands
        """
        self._controlLoopLock.acquire()
        if self.left_limb_enabled:
            self.left_limb_state.commandedq = []
            self.left_limb_state.difference = []
            self.left_limb_state.commandedqQueueStart = []
            self.left_limb_state.commandQueueTime = 0.0
            self.left_limb_state.commandedQueueDuration = 0.0
            self.left_limb_state.commandSent = True
            self.left_limb_state.commandQueue = False
            self.left_limb_state.commandeddq = []
            self.left_limb_state.cartesianDrive = False
            self.left_limb_state.impedanceControl = False
            self.left_limb_state.Xs = []
        if self.right_limb_enabled:
            self.right_limb_state.commandedq = []
            self.right_limb_state.difference = []
            self.right_limb_state.commandedqQueueStart = []
            self.right_limb_state.commandQueueTime = 0.0
            self.right_limb_state.commandedQueueDuration = 0.0
            self.right_limb_state.commandSent = True
            self.right_limb_state.commandQueue = False
            self.right_limb_state.commandeddq = []
            self.right_limb_state.cartesianDrive = False
            self.right_limb_state.impedanceControl = False
            self.right_limb_state.Xs = []
        if self.base_enabled:
            self.base_state.commandedVel = [0.0, 0.0]
            self.base_state.commandedTargetPosition = [] #[x, y, theta]
            self.base_state.commandSent = True
        if self.left_gripper_enabled:
            self.left_gripper_state.command_finger_set = [0.0, 0.0, 0.0, 0.0]
        if self.torso_enabled:
            self.torso_state.commandedHeight = 0.0
            self.torso_state.commandedTilt = 0.0
            self.torso_state.commandSent = True
            self.torso_state.leftLeg = 0
            self.torso_state.rightLeg = 0

        self._controlLoopLock.release()

    def _check_collision_linear(self,robot,q1,q2,discretization):
        """ Check collision between 2 robot configurations

        Parameters:
        -----------------
        robot: klampt robot model
        q1: a list of 6 doubles
        q2: a list of 6 doubles
        discretization: integers, the number of collision checks

        Return:
        -----------------
        bool
        """

        lin = np.linspace(0,1,discretization)
        initialConfig = robot.getConfig()
        diff = vectorops.sub(q2,q1)
        counter = 0
        for c in lin:
            Q=vectorops.madd(q1,diff,c)
            robot.setConfig(Q)
            collisions = self.collider.robotSelfCollisions(robot)
            colCounter = 0
            for col in collisions:
                colCounter = colCounter + 1
            if colCounter > 0:
                return True
            #add_ghosts(robot,vis,[robot.getConfig()],counter)
            counter = counter + 1
        robot.setConfig(initialConfig)
        return False

    def _check_collision_linear_adaptive(self,robot,q1,q2):
        """ Check collision between 2 robot configurations, but with adaptive number of collision checks

        Parameters:
        -----------------
        robot: klampt robot model
        q1: a list of 6 doubles
        q2: a list of 6 doubles

        Return:
        -----------------
        bool
        """
        discretization = math.ceil(vectorops.distance(q1,q2)/TRINAConfig.collision_check_interval)
        #print("N of discretization",discretization)
        lin = np.linspace(0,1,discretization + 1)
        initialConfig = robot.getConfig()
        diff = vectorops.sub(q2,q1)
        counter = 0
        iteration_counter = 0
        for c in lin:
            if iteration_counter > 0:
                #print('_check_collision_linear():started',c)
                Q=vectorops.madd(q1,diff,c)
                robot.setConfig(Q)
                collisions = self.collider.robotSelfCollisions(robot)
                colCounter = 0
                for col in collisions:
                    colCounter = colCounter + 1
                if colCounter > 0:
                    return True
                #add_ghosts(robot,vis,[robot.getConfig()],counter)
                counter = counter + 1
            else:
                iteration_counter = iteration_counter + 1
        robot.setConfig(initialConfig)
        return False

    def _limit_arm_position(self,config):
        """Modify the arm configuration to be within joint position limits

        Parameters:
        ---------------
        config: a list of 6 doubles

        Return:
        ---------------
        a list of 6 doubles
        """
        modified = []
        for i in range(6):
            if config[i] > TRINAConfig.limb_position_upper_limits[i]:
                modified.append(TRINAConfig.limb_position_upper_limits[i])
            elif config[i] < TRINAConfig.limb_position_lower_limits[i]:
                modified.append(TRINAConfig.limb_position_lower_limits[i])
            else:
                modified.append(config[i])
        return modified

    def _arm_is_in_limit(self,config,upper,lower):
        """Check if the config is within the limits

        Parameters:
        ---------------
        Lists of same length

        Return:
        ---------------
        bool
        """
        for [q,qu,ql] in zip(config,upper,lower):
            if q > qu or q < ql:
                return False

        return True

    def _left_limb_cartesian_drive(self,current_transform):
        ###TODO: robot_model config is no longer updated in the main loop ....

        """ Calculate the next position command for cartedian velocity drive

        Parameters:
        -------------
        current_transform: klampt rigid transform.

        Return:
        -------------
        result flag
        target_configuration, a list of 6 doubles

        """
        #print("current_transform:",current_transform)


        v = self.left_limb_state.cartesianDriveV
        w = self.left_limb_state.cartesianDriveW
        amount = self.dt * self.left_limb_state.driveSpeedAdjustment
        #print("Before:",self.left_limb_state.driveTransform)
        #print(v,amount,vectorops.mul(v,amount))
        target_transform = (so3.mul(so3.from_moment(vectorops.mul(w,amount)),\
            self.left_limb_state.driveTransform[0]),vectorops.add(\
            self.left_limb_state.driveTransform[1],vectorops.mul(v,amount)))

        #print("target_transform:",target_transform)
        ###debugging

        #print("After:",self.left_limb_state.driveTransform)
        #joint position limits from the joint speed limit

        if self.mode == 'Kinematic':
            C = self.dt
        elif self.mode == 'Physical':
            C = self.dt*30
        joint_upper_limits = vectorops.add(self.left_limb_state.sensedq,vectorops.mul(\
            TRINAConfig.limb_velocity_limits,C))
        joint_lower_limits = vectorops.add(self.left_limb_state.sensedq,vectorops.mul(\
            TRINAConfig.limb_velocity_limits,-C))
        if self.left_limb_state.cartesianMode == 0:
            goal = ik.objective(self.left_EE_link,R=target_transform[0],\
                t = vectorops.sub(target_transform[1],so3.apply(target_transform[0],self.left_limb_state.toolCenter)))
        elif self.left_limb_state.cartesianMode == 1:
            goal = ik.objective(self.left_EE_link,local = [0,0,0], \
                world = vectorops.sub(target_transform[1],so3.apply(target_transform[0],self.left_limb_state.toolCenter)))
        #elif self.left_limb_state.cartesianMode == 2:
        #                goal = ik.objective(self.left_EE_link,R=target_transform[0])
        initialConfig = self.robot_model.getConfig()
        res = ik.solve_nearby(goal,maxDeviation=0.5,activeDofs = self.left_active_Dofs,tol=0.000001)

        # print("\n\n\n number of iterations: ",ik.)
        failFlag = False
        if res:
            if self._arm_is_in_limit(self.robot_model.getConfig()[self.left_active_Dofs[0]:self.left_active_Dofs[5]+1],joint_upper_limits,joint_lower_limits):
                pass
            else:
                #print('failed because not in limit:',self.robot_model.getConfig()[self.left_active_Dofs[0]:self.left_active_Dofs[5]+1])
                failFlag = True
        else:
            failFlag = True
            #print("motion.controlLoop():IK solution not found")
        if failFlag:
            self.left_limb_state.driveSpeedAdjustment = self.left_limb_state.driveSpeedAdjustment - 0.1
            if self.left_limb_state.driveSpeedAdjustment < 0.001:
                self.left_limb_state.cartesianDrive = False
                logger.error('CartesianDrive IK has failed completely,exited..')
                print("motion.controlLoop():CartesianDrive IK has failed completely,exited..")
                return 0,0 # 0 means the IK has failed completely
            else:
                #print("motion.controlLoop():CartesianDrive IK has failed, next trying: ",\
                #   self.left_limb_state.driveSpeedAdjustment)
                return 1,0 # 1 means the IK has failed partially and we should do this again
        else:
            #print('success!')
            target_config = self.robot_model.getConfig()[self.left_active_Dofs[0]:self.left_active_Dofs[5]+1]
            self.left_limb_state.driveTransform = target_transform
            if self.left_limb_state.driveSpeedAdjustment < 1:
                self.left_limb_state.driveSpeedAdjustment = self.left_limb_state.driveSpeedAdjustment + 0.1

        self.robot_model.setConfig(initialConfig)

        return 2,target_config #2 means success..

    def _right_limb_cartesian_drive(self,current_transform):
        """ Calculate the next position command for cartedian velocity drive

        Parameters:
        -------------
        current_transform: klampt rigid transform.

        Return:
        -------------
        result flag
        target_configuration, a list of 6 doubles

        """
        v = self.right_limb_state.cartesianDriveV
        w = self.right_limb_state.cartesianDriveW
        amount = self.dt * self.right_limb_state.driveSpeedAdjustment
        #print("Before:",self.right_limb_state.driveTransform)
        #print(v,amount,vectorops.mul(v,amount))
        target_transform = (so3.mul(so3.from_moment(vectorops.mul(w,amount)),\
            self.right_limb_state.driveTransform[0]),vectorops.add(\
            self.right_limb_state.driveTransform[1],vectorops.mul(v,amount)))
        #print("After:",self.right_limb_state.driveTransform)
        #joint position limits from the joint speed limit

        if self.mode == 'Kinematic':
            C = self.dt
        elif self.mode == 'Physical':
            C = self.dt*30
        joint_upper_limits = vectorops.add(self.right_limb_state.sensedq,vectorops.mul(\
            TRINAConfig.limb_velocity_limits,C))
        joint_lower_limits = vectorops.add(self.right_limb_state.sensedq,vectorops.mul(\
            TRINAConfig.limb_velocity_limits,-C))
        if self.right_limb_state.cartesianMode == 0:
            goal = ik.objective(self.right_EE_link,R=target_transform[0],\
                t = vectorops.sub(target_transform[1],so3.apply(target_transform[0],self.right_limb_state.toolCenter)))
        elif self.right_limb_state.cartesianMode == 1:
            goal = ik.objective(self.right_EE_link,local = [0,0,0], \
                world = vectorops.sub(target_transform[1],so3.apply(target_transform[0],self.right_limb_state.toolCenter)))

        initialConfig = self.robot_model.getConfig()
        res = ik.solve_nearby(goal,maxDeviation=0.5,activeDofs = self.right_active_Dofs,tol=0.000001)
        failFlag = False
        if res:
            if self._arm_is_in_limit(self.robot_model.getConfig()[self.right_active_Dofs[0]:self.right_active_Dofs[5]+1],joint_upper_limits,joint_lower_limits):
                pass
            else:
                failFlag = True
        else:
            failFlag = True
            #print("motion.controlLoop():IK solution not found")
        if failFlag:
            self.right_limb_state.driveSpeedAdjustment = self.right_limb_state.driveSpeedAdjustment - 0.1
            if self.right_limb_state.driveSpeedAdjustment < 0.001:
                self.right_limb_state.cartesianDrive = False
                logger.error('CartesianDrive IK has failed completely,exited..')
                print("motion.controlLoop():CartesianDrive IK has failed completely,exited..")
                return 0,0 # 0 means the IK has failed completely
            else:
                #print("motion.controlLoop():CartesianDrive IK has failed, next trying: ",\
                #   self.right_limb_state.driveSpeedAdjustment)
                return 1,0 # 1 means the IK has failed partially and we should do this again
        else:

            target_config = self.robot_model.getConfig()[self.right_active_Dofs[0]:self.right_active_Dofs[5]+1]
            self.right_limb_state.driveTransform = target_transform
            if self.right_limb_state.driveSpeedAdjustment < 1:
                self.right_limb_state.driveSpeedAdjustment = self.right_limb_state.driveSpeedAdjustment + 0.1

        self.robot_model.setConfig(initialConfig)

        return 2,target_config #2 means success..

    def _right_limb_imdepance_drive(self):
        """Calculate the next goal for impedance position control
        Parameters:
        --------------

        Return:
        --------------
        Result flag
        target_config : list of doubles, the target limb config
        """
        wrench_raw = self.sensedRightEEWrench(frame = 'global') #need to correct for tool center torque
        Tcurr = self.sensedRightEETransform()
        mcurr = np.array(Tcurr[1]+so3.moment(Tcurr[0]))

        #print('mcurr:',mcurr)
        #calculate wrench at the tool frame
        wrench = wrench_raw[0:3] + vectorops.sub(wrench_raw[3:6],vectorops.cross(self.right_limb_state.toolCenter,wrench_raw[0:3]))
        #print('Wrench:',wrench)
        mcomm = np.array(self.right_limb_state.mg) + np.dot(self.right_limb_state.Kinv,np.array(wrench)) ##The EE command that we want to send
        diff_p = np.max(np.abs(mcomm[0:3] - mcurr[0:3]))
        diff_a = np.max(np.abs(mcomm[3:6] - mcurr[3:6]))
        direction_p = (mcomm[0:3] - mcurr[0:3])/diff_p
        direction_a = (mcomm[3:6] - mcurr[3:6])/diff_a
        #this is to avoid sending q whose difference is too small for the EE
        threshold_p = 0.004 #this caps the max velocity at 1m/s; threshould = 1m/s*dt
        threshold_a = 0.05
        m_tosend = np.zeros(6)
        if diff_p > threshold_p:
            m_tosend[0:3] = mcurr[0:3] + direction_p*threshold_p
        else:
            m_tosend[0:3] = mcomm[0:3]

        if diff_a > threshold_a:
            m_tosend[3:6] = mcurr[3:6] + direction_a*threshold_a
        else:
            m_tosend[3:6] = mcomm[3:6]

        T = (so3.from_moment(m_tosend[3:6].tolist()),m_tosend[0:3].tolist())
        C = self.dt*100
        joint_upper_limits = vectorops.add(self.right_limb_state.sensedq,vectorops.mul(\
            TRINAConfig.limb_velocity_limits,C))
        joint_lower_limits = vectorops.add(self.right_limb_state.sensedq,vectorops.mul(\
            TRINAConfig.limb_velocity_limits,-C))

        goal = ik.objective(self.right_EE_link,R=T[0],\
            t = vectorops.sub(T[1],so3.apply(T[0],self.right_limb_state.toolCenter)))

        initialConfig = self.robot_model.getConfig()
        res = ik.solve_nearby(goal,maxDeviation=0.5,activeDofs = self.right_active_Dofs,tol=0.000001)
        failFlag = False
        if res:
            if self._arm_is_in_limit(self.robot_model.getConfig()[self.right_active_Dofs[0]:self.right_active_Dofs[5]+1],joint_upper_limits,joint_lower_limits):
                pass
                #print('IK successful')
            else:
                failFlag = True
        else:
            failFlag = True
            #print("motion.controlLoop():IK solution not found")

        if failFlag:
            logger.error('CartesianDrive IK has failed y,exited..')
            print("motion.controlLoop():CartesianDrive IK has failed, exited this mode")
            return 0,0

        else:
            target_config = self.robot_model.getConfig()[self.right_active_Dofs[0]:self.right_active_Dofs[5]+1]

        self.robot_model.setConfig(initialConfig)

        return 1,target_

    def _simulate(self,wrench,m_inv,K,B,x_curr,x_dot_curr,x_g,dt,N):
        """
        Simulate a mass spring damper under external load, semi-implicit Euler integration

        Parameters:
        -----------------
        mass: 6x6 numpp array,inverse of the mass matrix
        K: 6x6 numpy array, spring constant matrix
        B, 6x6 numpy array, damping constant matrix
        x_curr: numpy array of 6, current position and angle
        x_dot_curr: numpy array of 6, curent speed
        x_g:numpy array of 6,current position target (neutral position of the spring)
        dt:simulation dt
        N: number of integration time steps

        Return:
        -----------------
        Xs: a 6xN np array
        """
        x = copy(x_curr)
        v = copy(x_dot_curr)
        Xs = np.zeros((6,N))
        for i in range(N):
            wrench_total = wrench + np.dot(K,x_g - x) - np.dot(B,v)
            a = np.dot(m_inv,wrench_total)

            print('wrench_total:',wrench_total[0])
            print('a',a[0])
            v = v + a*dt
            x = x + v*dt
            Xs[:,i] = x
        return Xs

    def _left_limb_imdepance_drive(self):
        """Calculate the next goal for impedance position control
        Parameters:
        --------------

        Return:
        --------------
        Result flag
        target_config : list of doubles, the target limb config
        """
        # print('flag')
        eps = 1e-5
        wrench_raw = self.sensedLeftEEWrench(frame = 'global') #need to correct for tool center torque
        #wrench_raw = [6,0,0,0,0,0]

        Tcurr = self.sensedLeftEETransform(self.left_limb_state.toolCenter)
        m_curr = np.array(Tcurr[1]+so3.moment(Tcurr[0]))
        v_curr = np.array(self.sensedLeftEEVelocity()[0] + self.sensedLeftEEVelocity()[1])
        #calculate wrench at the tool frame
        wrench = wrench_raw[0:3] + vectorops.sub(wrench_raw[3:6],vectorops.cross(self.left_limb_state.toolCenter,wrench_raw[0:3]))
        #a 6xN array
        Xs_new = self._simulate(wrench = wrench,m_inv = self.left_limb_state.m_inv,K = self.left_limb_state.K,B = self.left_limb_state.B,\
            x_curr = m_curr,x_dot_curr = v_curr,x_g = self.left_limb_state.mg,dt = self.dt,N = self.left_limb_state.N)
        Xs_new = Xs_new.tolist() #a list of 6 lists
        #debug
        # print('m_curr:',m_curr)
        # print('v_curr:',v_curr)
        # print('m_g:',self.left_limb_state.mg)
        print('Xs_new:',Xs_new[0])

        x_to_send = []
        for i in range(6):
            row = self.left_limb_state.Xs[i]
            if len(row) < 1:
                self.left_limb_state.Xs[i] = Xs_new[i]
                x_to_send.append(self.left_limb_state.Xs[i].pop(0))
                continue
            if math.fabs(self.left_limb_state.Xs[i][0] - m_curr[i]) < eps:
                ratio = 1.0
            else:
                ratio = (Xs_new[i][-1] - m_curr[i])/(self.left_limb_state.Xs[i][0] - m_curr[i])

            if i == 0:
                print(Xs_new[i][-1])
                print('ratio:',ratio)
            if ratio < 1.0:
                self.left_limb_state.Xs[i] = Xs_new[i]
            else:
                for j in range(self.left_limb_state.N):
                    if (Xs_new[i][j] - m_curr[i])/(self.left_limb_state.Xs[i][0] - m_curr[i]) > 1:
                        break
                if i==0:
                    print('j',j)
                self.left_limb_state.Xs[i] = Xs_new[i][j:self.left_limb_state.N]
            x_to_send.append(self.left_limb_state.Xs[i].pop(0))
        print('x_to_send:',x_to_send)
        ### old way of calculating T
        # mcomm = np.array(self.left_limb_state.mg) + np.dot(self.left_limb_state.Kinv,np.array(wrench)) ##The EE command that we want to send
        # diff_p = np.max(np.abs(mcomm[0:3] - mcurr[0:3]))
        # diff_a = np.max(np.abs(mcomm[3:6] - mcurr[3:6]))
        # direction_p = (mcomm[0:3] - mcurr[0:3])/diff_p
        # direction_a = (mcomm[3:6] - mcurr[3:6])/diff_a
        # #this is to avoid sending q whose difference is too small for the EE
        # threshold_p = 0.005 #this caps the max velocity at 1m/s; threshould = 1m/s*dt
        # threshold_a = 0.05
        # m_tosend = np.zeros(6)
        # if diff_p > threshold_p:
        #     m_tosend[0:3] = mcurr[0:3] + direction_p*threshold_p
        # else:
        #     m_tosend[0:3] = mcomm[0:3]
        #
        # if diff_a > threshold_a:
        #     m_tosend[3:6] = mcurr[3:6] + direction_a*threshold_a
        # else:
        #     m_tosend[3:6] = mcomm[3:6]
        # print(mcomm[3:6],mcurr[3:6])
        # T = (so3.from_moment(m_tosend[3:6].tolist()),m_tosend[0:3].tolist())

        T = (so3.from_moment(x_to_send[3:6]),x_to_send[0:3])
        C = self.dt*100
        joint_upper_limits = vectorops.add(self.left_limb_state.sensedq,vectorops.mul(\
            TRINAConfig.limb_velocity_limits,C))
        joint_lower_limits = vectorops.add(self.left_limb_state.sensedq,vectorops.mul(\
            TRINAConfig.limb_velocity_limits,-C))
        goal = ik.objective(self.left_EE_link,R=T[0],\
            t = vectorops.sub(T[1],so3.apply(T[0],self.left_limb_state.toolCenter)))

        initialConfig = self.robot_model.getConfig()
        res = ik.solve_nearby(goal,maxDeviation=0.5,activeDofs = self.left_active_Dofs,tol=0.000001)
        failFlag = False
        if res:
            if self._arm_is_in_limit(self.robot_model.getConfig()[self.left_active_Dofs[0]:self.left_active_Dofs[5]+1],joint_upper_limits,joint_lower_limits):
                pass
                #print('IK successful')
            else:
                failFlag = True
        else:
            failFlag = True
            #print("motion.controlLoop():IK solution not found")

        if failFlag:
            logger.error('CartesianDrive IK has failed y,exited..')
            print("motion.controlLoop():CartesianDrive IK has failed, exited this mode")
            return 0,0

        else:
            target_config = self.robot_model.getConfig()[self.left_active_Dofs[0]:self.left_active_Dofs[5]+1]

        self.robot_model.setConfig(initialConfig)

        return 1,target_config

    def _get_klampt_q(self,left_limb = [],right_limb = []):
        if left_limb:
            return TRINAConfig.get_klampt_model_q(self.codename,left_limb = left_limb, right_limb = self.right_limb_state.sensedq)
        elif right_limb:
            return TRINAConfig.get_klampt_model_q(self.codename,left_limb = self.left_limb_state.sensedq, right_limb = right_limb)
if __name__=="__main__":
    #################################
    robot = Motion(mode = 'Physical',components = ['left_limb'],codename = "anthrax")
    robot.startup()
    time.sleep(0.05)
    leftTuckedConfig = [0.7934980392456055, -2.541288038293356, -2.7833543555, 4.664876623744629, -0.049166981373, 0.09736919403076172]
    leftUntuckedConfig = [-0.2028,-2.1063,-1.610,3.7165,-0.9622,0.0974] #motionAPI format
    rightTuckedConfig = robot.mirror_arm_config(leftTuckedConfig)
    rightUntuckedConfig = robot.mirror_arm_config(leftUntuckedConfig)

    #move to untucked position
    robot.setLeftLimbPositionLinear(leftUntuckedConfig,5)
    #robot.setRightLimbPositionLinear(rightUntuckedConfig,5)
    #robot.setLeftLimbPosition(leftUntuckedConfig)
    #robot.setRightLimbPosition(rightUntuckedConfig)
    time.sleep(5)
    #startTime = time.time()

    K = np.array([[1000.0,0.0,0.0,0.0,0.0,0.0],\
                [0.0,1000.0,0.0,0.0,0.0,0.0],\
                [0.0,0.0,1000.0,0.0,0.0,0.0],\
                [0.0,0.0,0.0,5.0,0.0,0.0],\
                [0.0,0.0,0.0,0.0,5.0,0.0],\
                [0.0,0.0,0.0,0.0,0.0,5.0]])

    target = copy(robot.sensedLeftEETransform())
    target[1][0] += 0.0

    robot.setLeftEETransformImpedance(target,K,tool_center = [0.01,0,0])

    start_time = time.time()
    print('start')
    while time.time() - start_time < 20:
        time.sleep(0.01)
    print('stop')
    robot.setLeftLimbPositionLinear(leftUntuckedConfig,5)
    time.sleep(5)
    robot.shutdown()
